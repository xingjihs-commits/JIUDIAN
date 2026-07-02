# @deprecated — see tabs/frontdesk/ 模块
#
# 此文件已被拆分到前台模块包中：
#   入住/退房页面
#   交班页面
#   前台工作台枢纽
#   在住列表（占位）
#   超市收银（占位）
#   服务/送物（占位）
#
# 所有原有 import 路径保持兼容：
#   from tabs.frontdesk_tab import FrontdeskHubWidget, CheckinTab, ShiftTab

from tabs.frontdesk.shift_tab import ShiftTab
from tabs.frontdesk.checkin_tab import CheckinTab
from tabs.frontdesk.payment_v4 import PaymentMethodTiles
from tabs.frontdesk.hub_widget import FrontdeskHubWidget
