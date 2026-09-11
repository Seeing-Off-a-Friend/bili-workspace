# -*- coding: utf-8 -*-
"""Flask 网页入口 —— 只负责收参数、开线程、回传日志；下载逻辑全在 bili_core。

接口（和重构前保持一致，前端模板不用改）：
    GET  /                页面
    POST /download        表单 keyword / start / end / path → {"task_id": ...}
    POST /login           表单 timeout（可选）→ 打开浏览器登录并保存 {"task_id": ...}
    GET  /status/<id>     → {"status": running|completed|error, "log": ..., "message": ...}

启动： python web_app.py    然后浏览器打开 http://127.0.0.1:5000
"""

import threading
import uuid

from flask import Flask, jsonify, render_template, request

import bili_config
import bili_core
import bili_player

app = Flask(__name__)

tasks = {}                      # task_id -> {status, log, message}
_lock = threading.Lock()        # 多个下载线程同时写日志，得加锁


class TaskLogger:
    """把核心库的 log 回调写进任务日志（网页每 1.5 秒拉一次），同时打到控制台。"""

    def __init__(self, task_id):
        self.task_id = task_id

    def __call__(self, msg=""):
        text = str(msg)
        with _lock:
            task = tasks.get(self.task_id)
            if task is not None:
                task["log"] += text + "\n"
        print(text, flush=True)


def _finish(task_id, status, message):
    with _lock:
        task = tasks.get(task_id)
        if task is not None:
            task["status"] = status
            task["message"] = message


def run_task(task_id, keyword=None, start=None, end=None, save_path=None, urls=None):
    """后台线程：调用共用核心，把结果写回任务表。

    urls 非空 → 按网址下载；否则按关键词搜索下载。
    """
    log = TaskLogger(task_id)
    try:
        # 网页版不自动播放，改由结果列表上的按钮触发（路径只从服务端任务记录里取）
        if urls:
            result = bili_core.download_by_urls(urls, save_path, log=log,
                                                start=start, end=end)
        else:
            result = bili_core.download_range(keyword, start, end, save_path, log=log)
        with _lock:
            task = tasks.get(task_id)
            if task is not None:
                task["files"] = result["files"]
                task["save_path"] = save_path
        if result["failed"]:
            _finish(task_id, "completed",
                    f"下载结束：成功 {result['ok']} 个，失败 {result['failed']} 个（详见日志）")
        else:
            _finish(task_id, "completed", f"下载完成，共 {result['ok']} 个视频")
    except Exception as e:
        log(f"[错误] {e}")
        _finish(task_id, "error", f"失败: {e}")


def run_login(task_id, timeout):
    """后台线程：打开浏览器等用户登录，把结果写回任务表。"""
    log = TaskLogger(task_id)
    try:
        import bili_login
        session = bili_login.interactive_login(timeout=timeout, log=log)
        if session:
            who = session.get("uname") or "未知账号"
            _finish(task_id, "completed",
                    f"登录成功：{who}（{'大会员' if session.get('vip') else '普通账号'}），已保存登录态")
        else:
            _finish(task_id, "error", "没检测到登录（超时或未登录），原有配置继续可用")
    except Exception as e:
        log(f"[错误] {e}")
        _finish(task_id, "error", f"失败: {e}")


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download', methods=['POST'])
def start_download():
    keyword = request.form.get('keyword', '').strip()
    urls_text = request.form.get('urls', '').strip()
    start_str = request.form.get('start', '').strip()
    end_str = request.form.get('end', '').strip()
    save_path = request.form.get('path', '').strip()

    if not save_path:
        return jsonify({'error': '请填写保存路径'}), 400

    # ---------- 网址模式 ----------
    if urls_text:
        urls = bili_core.split_urls(urls_text)
        if not urls:
            return jsonify({'error': '请粘贴至少一个 B站网址'}), 400

        start = end = None
        for raw, name in ((start_str, '开始'), (end_str, '结束')):
            if raw:
                try:
                    value = int(raw)
                except ValueError:
                    return jsonify({'error': f'{name}序号请输入数字'}), 400
                if value < 1:
                    return jsonify({'error': f'{name}序号要 ≥ 1'}), 400
                if name == '开始':
                    start = value
                else:
                    end = value
        if start and end and end < start:
            return jsonify({'error': '结束序号须 ≥ 开始序号'}), 400

        task_id = str(uuid.uuid4())
        with _lock:
            tasks[task_id] = {'status': 'running', 'log': '',
                              'message': f'开始处理 {len(urls)} 个网址...'}
        threading.Thread(target=run_task, daemon=True,
                         args=(task_id,),
                         kwargs={'save_path': save_path, 'urls': urls,
                                 'start': start, 'end': end}).start()
        return jsonify({'task_id': task_id, 'urls': len(urls)})

    # ---------- 搜索模式 ----------
    if not keyword:
        return jsonify({'error': '请输入搜索关键词，或切到「按网址下载」粘贴链接'}), 400
    if not start_str or not end_str:
        return jsonify({'error': '请填写视频范围'}), 400

    try:
        start = int(start_str)
        end = int(end_str)
    except ValueError:
        return jsonify({'error': '视频范围请输入数字'}), 400

    if start < 1 or end < start:
        return jsonify({'error': '范围无效，结束须 ≥ 开始，且开始 ≥ 1'}), 400

    task_id = str(uuid.uuid4())
    with _lock:
        tasks[task_id] = {'status': 'running', 'log': '', 'message': '正在启动...'}

    threading.Thread(target=run_task, daemon=True,
                     args=(task_id, keyword, start, end, save_path)).start()

    return jsonify({'task_id': task_id})


@app.route('/login', methods=['POST'])
def start_login():
    """点一下按钮就能开浏览器登录 —— 给不想碰命令行的人用。"""
    timeout_str = request.form.get('timeout', '').strip()
    try:
        timeout = float(timeout_str) if timeout_str else 180.0
    except ValueError:
        return jsonify({'error': '等待秒数请填数字'}), 400
    timeout = max(30.0, min(timeout, 900.0))

    task_id = str(uuid.uuid4())
    with _lock:
        tasks[task_id] = {'status': 'running', 'log': '',
                          'message': '正在打开浏览器，请在弹窗里登录...'}

    thread = threading.Thread(target=run_login, args=(task_id, timeout), daemon=True)
    thread.start()

    return jsonify({'task_id': task_id})


@app.route('/open', methods=['POST'])
def open_item():
    """打开任务里某个文件：action=play 播放，action=folder 打开所在目录，
    action=save_folder 打开保存目录。

    路径一律从服务端的任务记录里查，前端只能传"第几个"，避免变成任意路径执行入口。
    """
    task_id = request.form.get('task_id', '').strip()
    action = request.form.get('action', 'play').strip()
    index_str = request.form.get('index', '').strip()

    with _lock:
        task = tasks.get(task_id)
        files = list(task.get('files') or []) if task else []
        save_path = (task or {}).get('save_path')

    if task is None:
        return jsonify({'error': '找不到这个任务'}), 404

    if action == 'save_folder':
        target = save_path
        if not target:
            return jsonify({'error': '这个任务没有记录保存目录'}), 400
        try:
            folder = bili_player.open_folder(target, select=False)
        except bili_player.OpenError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify({'ok': True, 'path': folder})

    try:
        index = int(index_str) if index_str else 0
    except ValueError:
        return jsonify({'error': '文件序号不对'}), 400

    if not files:
        return jsonify({'error': '这个任务还没有下载好的文件'}), 400
    if not 0 <= index < len(files):
        return jsonify({'error': '文件序号超出范围'}), 400

    path = files[index]
    try:
        if action == 'folder':
            bili_player.open_folder(path)
        else:
            bili_player.play(path, player=bili_config.PLAYER or None)
    except bili_player.OpenError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'打开失败: {e}'}), 500

    return jsonify({'ok': True, 'path': path})


@app.route('/status/<task_id>')
def get_status(task_id):
    with _lock:
        task = tasks.get(task_id)
        if task is None:
            return jsonify({'status': 'not_found'}), 404
        return jsonify(dict(task))


if __name__ == '__main__':
    print("网页版已启动：请用浏览器打开 http://127.0.0.1:5000")
    print("（下载时会自动弹出 Chrome 窗口去搜索，请不要关闭它）")
    app.run(host='127.0.0.1', port=5000, debug=False)
