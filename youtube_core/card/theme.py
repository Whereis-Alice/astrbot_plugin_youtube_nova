"""卡片主题配方：把结构、装饰与排版策略声明化。

一套主题（skin）= 背景做法 + 装饰通道 + 面板风格 + 区块顺序 + 每个区块的变体。
主题只描述怎么排，颜色一律来自 palette 模块，尺寸一律来自 metrics 模块，
因此 theme(深/浅) x skin(6) x layout(4) 的 48 种组合都能真正生效。
另有一个哨兵值 auto（跟随平台）；本插件只解析 YouTube，因此固定落到
YouTube 观看页皮肤。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .palette import RGB, hex_to_rgb

# ============================ 主题配方 ============================


@dataclass(frozen=True, slots=True)
class ThemeRecipe:
    """一套视觉语言的声明式配方。"""

    key: str
    label: str
    palette_key: str                 # 取哪一组 Palette
    density: str                     # airy / normal / compact

    backdrop: str                    # mesh / paper / graphite / wall / midnight / plain
    ornament: tuple[str, ...]        # grain / fiber / halftone / grid / scan / vignette / bloom
    panel: str                       # glass / none / outline / card / bar
    ornament_frame: str              # none / corner / keyline / bracket

    eyebrow: str                     # chip / rule / bracket / plate
    identity: str                    # avatar_left / stacked / minimal / plate
    headline: str                    # display / tight / upper
    body: str                        # plain / dropcap / indent
    media: str                       # editorial / mosaic / framed / window / feed
    stats: str                       # chips / ledger / bars / inline
    comments: str                    # cards / thread / quote
    footer: str                      # rule / plate / minimal / ledger

    tracking_eyebrow: float = 0.0
    tracking_headline: float = 0.0
    uppercase_eyebrow: bool = False
    headline_scale: float = 1.0
    accent_source: str = "platform"  # platform / fixed / blend
    accent_fixed: RGB = (90, 118, 247)
    #: 是否按深浅模式微调强调色（品牌色必须原样呈现的主题设为 False）
    accent_adjust: bool = True
    #: 辅助强调色（页脚链接 / 次级标记），None 时回落到主强调色
    accent_alt: RGB | None = None
    #: 圆角整体缩放：仿站点视觉时用来还原对方的圆角语言
    radius_scale: float = 1.0
    caption_numbering: bool = False  # 媒体窗编号 01/03
    order: tuple[str, ...] = (
        "eyebrow",
        "identity",
        "headline",
        "body",
        "media",
        "stats",
        "quote",
        "warnings",
        "comments",
        "footer",
    )

    def with_accent_fixed(self, color: RGB) -> "ThemeRecipe":
        return replace(self, accent_fixed=color)


_DEFAULT_ORDER = (
    "eyebrow",
    "identity",
    "headline",
    "body",
    "media",
    "stats",
    "quote",
    "warnings",
    "comments",
    "footer",
)


AURORA = ThemeRecipe(
    key="aurora",
    label="极光",
    palette_key="aurora",
    density="normal",
    backdrop="mesh",
    ornament=("bloom", "grain"),
    panel="glass",
    ornament_frame="none",
    eyebrow="chip",
    identity="avatar_left",
    headline="display",
    body="plain",
    media="editorial",
    stats="chips",
    comments="cards",
    footer="rule",
    tracking_eyebrow=0.6,
    accent_source="platform",
    order=_DEFAULT_ORDER,
)

BROADSHEET = ThemeRecipe(
    key="broadsheet",
    label="报章",
    palette_key="broadsheet",
    density="airy",
    backdrop="paper",
    ornament=("fiber",),
    panel="none",
    ornament_frame="keyline",
    eyebrow="rule",
    identity="minimal",
    headline="tight",
    body="dropcap",
    media="editorial",
    stats="ledger",
    comments="quote",
    footer="ledger",
    tracking_eyebrow=2.6,
    tracking_headline=-0.4,
    uppercase_eyebrow=True,
    headline_scale=1.12,
    accent_source="fixed",
    accent_fixed=hex_to_rgb("#B3261E"),
    order=(
        "eyebrow",
        "headline",
        "identity",
        "body",
        "media",
        "stats",
        "quote",
        "warnings",
        "comments",
        "footer",
    ),
)

TELEMETRY = ThemeRecipe(
    key="telemetry",
    label="测控",
    palette_key="telemetry",
    density="compact",
    backdrop="graphite",
    ornament=("grid", "scan"),
    panel="outline",
    ornament_frame="corner",
    eyebrow="bracket",
    identity="plate",
    headline="upper",
    body="indent",
    media="window",
    stats="bars",
    comments="thread",
    footer="plate",
    tracking_eyebrow=1.8,
    uppercase_eyebrow=True,
    headline_scale=0.94,
    accent_source="fixed",
    accent_fixed=hex_to_rgb("#39D8C8"),
    caption_numbering=True,
    order=(
        "eyebrow",
        "identity",
        "headline",
        "stats",
        "body",
        "media",
        "quote",
        "warnings",
        "comments",
        "footer",
    ),
)

GALLERY = ThemeRecipe(
    key="gallery",
    label="展陈",
    palette_key="gallery",
    density="airy",
    backdrop="wall",
    ornament=("fiber", "vignette"),
    panel="card",
    ornament_frame="keyline",
    eyebrow="plate",
    identity="stacked",
    headline="tight",
    body="indent",
    media="framed",
    stats="inline",
    comments="quote",
    footer="minimal",
    tracking_eyebrow=3.2,
    uppercase_eyebrow=True,
    headline_scale=1.06,
    accent_source="fixed",
    accent_fixed=hex_to_rgb("#9A7B3F"),
    caption_numbering=True,
    order=(
        "eyebrow",
        "media",
        "headline",
        "identity",
        "body",
        "stats",
        "quote",
        "warnings",
        "comments",
        "footer",
    ),
)

NOCTURNE = ThemeRecipe(
    key="nocturne",
    label="夜曲",
    palette_key="nocturne",
    density="normal",
    backdrop="midnight",
    ornament=("bloom", "scan", "vignette"),
    panel="glass",
    ornament_frame="none",
    eyebrow="plate",
    identity="stacked",
    headline="display",
    body="indent",
    media="mosaic",
    stats="ledger",
    comments="cards",
    footer="plate",
    tracking_eyebrow=1.2,
    headline_scale=1.1,
    accent_source="blend",
    accent_fixed=hex_to_rgb("#A07BFF"),
    order=(
        # 夜曲：标题先行、图像居中，与极光的"作者优先"形成区分
        "eyebrow",
        "headline",
        "identity",
        "media",
        "body",
        "stats",
        "quote",
        "warnings",
        "comments",
        "footer",
    ),
)

YOUTUBE = ThemeRecipe(
    key="youtube",
    label="YouTube",
    palette_key="youtube",
    density="compact",
    backdrop="plain",
    ornament=(),
    panel="card",
    ornament_frame="none",
    # YouTube 观看页专用详情结构：顶栏、作者、正文、封面、数据、评论与操作栏。
    eyebrow="detail_top",
    identity="detail",
    headline="detail_post",
    body="detail",
    media="detail",
    stats="detail",
    comments="detail",
    footer="detail",
    tracking_eyebrow=0.0,
    headline_scale=1.0,
    accent_source="fixed",
    accent_fixed=hex_to_rgb("#FF0033"),
    accent_adjust=False,
    accent_alt=hex_to_rgb("#3EA6FF"),
    radius_scale=0.9,
    order=(
        "eyebrow",
        "identity",
        "headline",
        "body",
        "media",
        "ipnote",
        "quote",
        "warnings",
        "comments",
        "footer",
    ),
)

THEMES: dict[str, ThemeRecipe] = {
    "aurora": AURORA,
    "broadsheet": BROADSHEET,
    "telemetry": TELEMETRY,
    "gallery": GALLERY,
    "nocturne": NOCTURNE,
    "youtube": YOUTUBE,
}

THEME_KEYS: tuple[str, ...] = tuple(THEMES)

#: 旧配置值 -> 新主题 key（保持向后兼容，不让用户已有配置失效）
THEME_ALIASES: dict[str, str] = {
    # v1.4 及更早的英文 key
    "nova": "aurora",
    "editorial": "broadsheet",
    "signal": "telemetry",
    "poster": "gallery",
    "neon": "nocturne",
    # 英文别名
    "default": "aurora",
    "glass": "aurora",
    "paper": "broadsheet",
    "news": "broadsheet",
    "newspaper": "broadsheet",
    "terminal": "telemetry",
    "hud": "telemetry",
    "museum": "gallery",
    "exhibit": "gallery",
    "archive": "gallery",
    "night": "nocturne",
    "midnight": "nocturne",
    # 独立插件早期曾误带 B 站 / X 皮肤；旧值统一迁移到 YouTube。
    "feed": "youtube",
    "timeline": "youtube",
    "social": "youtube",
    "stream": "youtube",
    "bili": "youtube",
    "b站": "youtube",
    # 中文别名（含 v1.4 旧中文名）
    "极光": "aurora",
    "极光玻璃": "aurora",
    "nova 原生": "aurora",
    "nova原生": "aurora",
    "原生": "aurora",
    "报章": "broadsheet",
    "报章排印": "broadsheet",
    "编辑室": "broadsheet",
    "杂志": "broadsheet",
    "测控": "telemetry",
    "测控面板": "telemetry",
    "信号终端": "telemetry",
    "终端": "telemetry",
    "展陈": "gallery",
    "展陈画廊": "gallery",
    "海报档案": "gallery",
    "海报": "gallery",
    "画廊": "gallery",
    "夜曲": "nocturne",
    "夜曲霓虹": "nocturne",
    "霓虹夜景": "nocturne",
    "霓虹": "nocturne",
    "信息流": "youtube",
    "动态流": "youtube",
    "时间线": "youtube",
    "b站动态": "youtube",
    "哔哩哔哩": "youtube",
    "哔哩哔哩风格": "youtube",
    "哔哩": "youtube",
    "小电视": "youtube",
    "b站风格": "youtube",
    "bilibili风格": "youtube",
    "bilibili": "youtube",
    "twitter": "youtube",
    "tweet": "youtube",
    "推特": "youtube",
    "x（推特）": "youtube",
    "x(推特)": "youtube",
    "蓝鸟": "youtube",
    "小蓝鸟": "youtube",
    "twitter风格": "youtube",
    "x风格": "youtube",
    "推特风格": "youtube",
    "x": "youtube",
    "yt": "youtube",
    "油管": "youtube",
    "油管风格": "youtube",
    "youtube风格": "youtube",
    "呦土笨": "youtube",
}


def resolve_theme_key(value: str | None) -> str:
    """把任意历史 / 中文 / 英文写法归一到 6 个主题 key 之一。"""
    if not value:
        return "aurora"
    raw = str(value).strip()
    lowered = raw.lower()
    if lowered in THEMES:
        return lowered
    if lowered in THEME_ALIASES:
        return THEME_ALIASES[lowered]
    if raw in THEME_ALIASES:
        return THEME_ALIASES[raw]
    return "aurora"


def get_theme(value: str | None) -> ThemeRecipe:
    return THEMES[resolve_theme_key(value)]

#: 「跟随平台」哨兵 key：不是一套真皮肤，渲染时按 model.platform_key 现场决定。
AUTO_THEME_KEY = "auto"

#: 「跟随平台」的英文 / 中文写法
AUTO_THEME_ALIASES: frozenset[str] = frozenset(
    {
        "auto",
        "follow",
        "platform",
        "follow_platform",
        "跟随平台",
        "自动",
        "自动跟随",
        "随平台",
        "平台自适应",
    }
)


def is_auto_theme(value: str | None) -> bool:
    """判断配置值是否表示「跟随平台」。"""
    if not value:
        return False
    raw = str(value).strip()
    return raw.lower() in AUTO_THEME_ALIASES or raw in AUTO_THEME_ALIASES


def resolve_theme_key_for_platform(
    value: str | None, platform_key: str | None
) -> str:
    """在 resolve_theme_key 之外多处理一件事：「跟随平台」。

    只有配置成 auto 时才看 platform_key，否则行为与 :func:`resolve_theme_key`
    完全一致——用户明确选了皮肤就绝不擅自替换。
    """
    if not is_auto_theme(value):
        return resolve_theme_key(value)
    del platform_key
    return "youtube"


# ============================ 布局预设 ============================


@dataclass(frozen=True, slots=True)
class LayoutPreset:
    """布局预设：改变区块的空间组织方式，对所有主题都生效。"""

    key: str
    label: str
    #: 主体是否分为侧栏 + 主栏（12 栏网格里侧栏占几栏）
    sidebar_span: int = 0
    #: 是否使用全幅 hero + 压字
    immersive_hero: bool = False
    #: 标题字号乘数
    headline_scale: float = 1.0
    #: 密度覆盖（None = 用主题自己的密度）
    density: str | None = None
    #: 媒体区高度乘数
    media_scale: float = 1.0
    #: 强制媒体排布（None = 由数量与宽高比自动决定）
    media_mode: str | None = None
    #: 侧栏放哪些区块
    sidebar_blocks: tuple[str, ...] = ()
    #: 是否隐藏部分区块
    drop_blocks: tuple[str, ...] = ()
    #: 额外强制横跨整幅的区块（不进侧栏行，避免主栏过长把侧栏拉出大片空白）
    full_width_blocks: tuple[str, ...] = ()


LAYOUTS: dict[str, LayoutPreset] = {
    "standard": LayoutPreset(
        key="standard",
        label="标准",
        headline_scale=1.0,
    ),
    "magazine": LayoutPreset(
        key="magazine",
        label="杂志",
        sidebar_span=5,
        headline_scale=1.06,
        density="airy",
        sidebar_blocks=("identity", "stats", "quote"),
        full_width_blocks=("media",),
    ),
    "immersive": LayoutPreset(
        key="immersive",
        label="沉浸",
        immersive_hero=True,
        headline_scale=1.18,
        media_scale=1.16,
    ),
    "feed": LayoutPreset(
        key="feed",
        label="紧凑流",
        headline_scale=0.9,
        density="compact",
        media_scale=0.88,
        media_mode="mosaic",
    ),
}

LAYOUT_KEYS: tuple[str, ...] = tuple(LAYOUTS)

LAYOUT_ALIASES: dict[str, str] = {
    "default": "standard",
    "normal": "standard",
    "标准": "standard",
    "常规": "standard",
    "杂志": "magazine",
    "分栏": "magazine",
    "mag": "magazine",
    "沉浸": "immersive",
    "沉浸式": "immersive",
    "全幅": "immersive",
    "immerse": "immersive",
    "紧凑": "feed",
    "紧凑流": "feed",
    "动态流": "feed",
    "compact": "feed",
    "timeline": "feed",
}


def resolve_layout_key(value: str | None) -> str:
    if not value:
        return "standard"
    raw = str(value).strip()
    lowered = raw.lower()
    if lowered in LAYOUTS:
        return lowered
    if lowered in LAYOUT_ALIASES:
        return LAYOUT_ALIASES[lowered]
    if raw in LAYOUT_ALIASES:
        return LAYOUT_ALIASES[raw]
    return "standard"


def get_layout(value: str | None) -> LayoutPreset:
    return LAYOUTS[resolve_layout_key(value)]


def resolve_mode(theme: str | None) -> str:
    """把配置里的 theme（深色 / 浅色）归一为 dark / light。"""
    raw = str(theme or "dark").strip().lower()
    if raw in ("light", "浅色", "亮色", "白", "day", "白天"):
        return "light"
    return "dark"
