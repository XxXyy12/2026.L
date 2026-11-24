"""全部页面选择器集中在此，页面改版只改这里。

现状：网商园与闲鱼的真实 DOM 需要实测校准（见 PROGRESS 阶段 4/7）。
已确认的文案来自需求与用户描述，选择器以“文案 + 结构候选”形式给出，
实测后补齐 CSS/属性选择器，并在注释中标注实测日期与截图。
"""

from __future__ import annotations

# ===== 网商园（实测：2026-11-23，www.wsy.com）=====
WSY = {
    "home_url": "https://www.wsy.com/",
    "login_entry_texts": ["登录", "请登录"],
    "username_inputs": [
        'input[placeholder*="手机"]',
        'input[placeholder*="账号"]',
        'input[name="username"]',
        'input[type="text"]',
    ],
    "password_inputs": ['input[type="password"]', 'input[placeholder*="密码"]'],
    "login_submit_texts": ["登录", "立即登录"],
    "logged_in_markers": ["退出", "我的订单", "个人中心"],
    "gold_stall_texts": ["金牌档口"],
    "new_30d_texts": ["30日新品", "30 日新品", "30天新品"],
    "menswear_texts": ["男装"],
    "one_click_download_texts": ["一键下载"],
    "detail_row_text": "当前的详情图",
    "detail_row_download_texts": ["下载"],
    "download_modal_frame_keyword": "downloads.htm",
    "shop_ranking_path": "/ranking.htm",
    "product_card_selectors": [
        ".item-c",
        ".item",
        ".goods-item",
    ],
    "shop_card_selectors": [
        "a[href*='shop']",
        ".shop-item a",
        ".stall-item a",
    ],
}

# ===== 闲鱼（实测：2026-11-23，www.goofish.com）=====
XIANYU = {
    "home_url": "https://www.goofish.com/",
    "publish_url": "https://www.goofish.com/publish",
    "login_entry_texts": ["登录"],
    "password_tab_text": "密码登录",
    "logged_in_markers": ["发闲置", "发布闲置"],
    "publish_entry_texts": ["发闲置", "发布闲置"],
    "upload_entry_texts": ["添加首图", "添加图片"],
    "upload_inputs": ['input[type="file"]'],
    "desc_inputs": ["textarea", "div[contenteditable='true']"],
    "price_label_texts": ["价格", "售价"],
    "location_texts": ["所在地"],
    "form_item_selectors": [".ant-form-item", ".ant-form-item-row"],
    "select_trigger_selector": ".ant-select-selector",
    "select_option_selector": ".ant-select-dropdown:visible .ant-select-item-option",
    "condition_label_text": "成色",
    "condition_new_option": "全新",
    "location_label_text": "宝贝所在地",
    "placeholder_text": "请选择",
    "submit_texts": ["发布", "确认发布"],
    "publish_success_markers": ["发布成功", "已发布"],
}
