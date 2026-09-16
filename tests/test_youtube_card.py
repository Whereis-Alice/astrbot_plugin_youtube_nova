from youtube_core.card import build_model
from youtube_core.card.engine import render_card_image
from youtube_core.rika_render.data import Author, ParseResult, Platform


def test_youtube_auto_skin_renders_a_nonblank_card() -> None:
    result = ParseResult(
        platform=Platform(name="youtube", display_name="YouTube"),
        author=Author(name="Nova Channel", description="@nova"),
        title="YouTube Nova 独立插件渲染测试",
        text="播放器信息、统计和评论应使用 YouTube 观看页语义。",
        timestamp=1_756_000_000,
        url="https://youtu.be/dQw4w9WgXcQ",
        extra={
            "content_type": "视频",
            "stats_line": "👀 12.3万 👍 8547 💬 453",
            "hot_comments": [
                {
                    "username": "评论用户",
                    "likes": 41,
                    "time": "3天前",
                    "message": "独立插件卡片渲染正常。",
                }
            ],
        },
    )
    model = build_model(
        result,
        {"avatar": None, "hero": None, "grid": [], "comment_avatars": {}},
        watermark="YouTube Nova",
    )

    image = render_card_image(
        model,
        width=640,
        mode="dark",
        theme_key="auto",
        layout_key="feed",
    )

    assert image.width == 640
    assert image.height > 300
    assert len(image.convert("RGB").getcolors(maxcolors=1_000_000) or []) > 20
