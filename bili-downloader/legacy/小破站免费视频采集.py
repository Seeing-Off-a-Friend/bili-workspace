import requests
import os
import re
from lxml import etree
from moviepy import VideoFileClip

# 视频文件搜mp4/video,音频文件搜audio
GeBuLin_url = "https://www.bilibili.com/video/BV1Bvbv6TEtT/?spm_id_from=333.337.search-card.all.click&vd_source=207368ff5b2102db4638b2794d020202"
# 模拟浏览器。原文件此处硬编码了一份 Cookie（已失效），现改为从环境变量读。
GeBuLin_headers = {
    "user-agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "cookie": os.environ.get("BILI_COOKIE", ""),
    "referer":'https://www.bilibili.com/v/popular/rank/all'
}
# 发送请求,并在此处加入请求头
response = requests.get(url = GeBuLin_url ,headers = GeBuLin_headers)
#获取视频标题
data = etree.HTML(response.text)
titles = data.xpath('//div[@class="video-info-title-inner"]/h1/@title')[0]

# 正则表达式，也可以用json，但是json如果嵌套非常深，会很麻烦
gz = r'"baseUrl":"(.+?)"'     # ？代表不知道有多少个该变量，一键提取，.是一个字符,baseUrl则是我们要查找的
urls = re.findall(gz,response.text)

# 取视频和声音的地址
video_url = urls[0]
audio_url = urls[-1]
lujing = r"F:\用户\视频"
# ------------------------------视频-------------------------------------
print("正在下载视频")
video_response = requests.get(url=video_url,headers=GeBuLin_headers)
wen_jian = os.path.join(lujing,"video.m4s")
with open(wen_jian,"wb") as f:
    f.write(video_response.content)
print("下载成功")
# #-------------------------------音频-------------------------------------
# print("正在下载音频")
# audio_response = requests.get(url=audio_url,headers=GeBuLin_headers)
# wen_jian = os.path.join(lujing,"audio.m4s")
# with open(wen_jian,"wb") as f:
#     f.write(audio_response.content)
# print("下载成功")
# #合并视频，只需要两行代码就搞定，但是需要学一个新的库 moviepy ---pip install moviepy
# #先把内容读取进来
# # 读取视频并合并音频
# output_path = os.path.join(lujing, f"{titles}.mp4")
# video = VideoFileClip(os.path.join(lujing, "video.m4s"))
# video.write_videofile(output_path, audio=os.path.join(lujing, "audio.m4s"))
#
# # 合并完成后，删除临时文件
# os.remove(os.path.join(lujing, "video.m4s"))
# os.remove(os.path.join(lujing, "audio.m4s"))
# #写出一个全新的文件，并且把mp3合并进去
# #因为B站有音视频分离，所以才说了一个合并音视频
# print("合并完成")