房间立牌模板（插卡用）
========================

在 Solid 里打开「批量二维码」或「生成二维码」窗口，
蓝色提示框里会显示本文件夹的完整路径，并可一键打开。

1. 内置 A7 竖版极简模板（74×105mm，300dpi）。
   可直接导出立牌；若要自己的风格，把设计图保存为：
   standee_template.png
   （须同为 A7 竖版，并相应修改 template.json 坐标）

2. 底板上留出一块空白给「二维码」、上方留白给「房号」。
   用 PS / 画图打开 PNG，量出二维码区域左上角 (x,y) 和边长，改 template.json 里 slots.qr。

3. 在 Solid 里：批量二维码 →「导出全部立牌图」
   生成 room_101_standee.png 等，直接插入立牌透明插槽打印。

4. 二维码内容是固定活码（/r/xxxx），换 Bot 不用重印立牌。

若暂时没有您的图，可运行：
  python scripts/generate_default_standee_template.py
会生成一张可用的默认竖版底板供试印。
