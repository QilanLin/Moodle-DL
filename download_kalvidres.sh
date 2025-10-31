#!/bin/bash
# Kalvidres 视频下载脚本
# 视频: 01-intro (Week 1 Introduction)

COOKIES="/Users/linqilan/CodingProjects/Cookies.txt"
OUTPUT_DIR="/Users/linqilan/CodingProjects/Videos"
mkdir -p "$OUTPUT_DIR"

echo "下载 Kalvidres 视频..."
echo ""

# 方法 1: 使用 yt-dlp (推荐)
echo "方法 1: yt-dlp (自动选择最佳质量)"
yt-dlp --cookies "$COOKIES" \
  "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619" \
  -o "$OUTPUT_DIR/Week1-01-intro.%(ext)s" \
  --write-subs --sub-lang en

# 方法 2: 直接下载最高质量 MP4 (1920x1080)
echo ""
echo "方法 2: 直接下载 (1920x1080, 1378 kbps)"
curl -L -b "$COOKIES" \
  "https://cdnapisec.kaltura.com/p/2368101/sp/236810100/playManifest/entryId/1_smw4vcpg/flavorId/1_wkhc74fb/format/url/protocol/https/a.mp4" \
  -o "$OUTPUT_DIR/Week1-01-intro-1080p.mp4"

# 方法 3: 下载 HLS 流 (自适应码率)
echo ""
echo "方法 3: HLS 流下载"
ffmpeg -cookies "$(cat $COOKIES | grep -v '^#' | awk '{print $6"="$7}' | tr '\n' ';')" \
  -i "https://cdnapisec.kaltura.com/p/2368101/sp/2368101/playManifest/entryId/1_smw4vcpg/flavorIds/1_zn0l8j0w,1_qs3gu76b,1_6z73h0y8,1_w9vu0rz1,1_wkhc74fb/deliveryProfileId/23732/protocol/https/format/applehttp/a.m3u8" \
  -c copy "$OUTPUT_DIR/Week1-01-intro-hls.mp4"

echo ""
echo "✅ 下载完成！"
echo "文件保存在: $OUTPUT_DIR"
