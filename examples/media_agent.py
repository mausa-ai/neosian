"""Media generation agent for eval demo.

This agent has tool IMPLEMENTATIONS only.
Prompts and descriptions come from YAML configs during eval.

Usage:
    # With eval (prompt variants)
    neosian eval examples/eval_media_prompts.yaml

    # Direct playground (uses inline prompts)
    neosian playground examples/media_agent.py
"""

from typing import Annotated, Literal

from neosian import AgentConfig, Desc, Max, Min, Tool, ToolResult


@Tool(
    name="generate_image",
    description="Create or edit images from text prompts.",
)
async def generate_image(
    prompt: Annotated[str, Desc("Detailed description of the image to generate")],
    image_urls: Annotated[
        list[str] | None, Desc("URLs of images to edit (for edit mode)")
    ] = None,
    quality: Annotated[
        Literal["standard", "high"], Desc("Image quality level")
    ] = "standard",
    aspect_ratio: Annotated[
        Literal["1:1", "16:9", "9:16", "4:3", "3:4"],
        Desc("Output aspect ratio"),
    ] = "1:1",
) -> ToolResult[dict[str, str]]:
    """Generate or edit an image."""
    mode = "edit" if image_urls else "generate"
    return ToolResult.ok({
        "mode": mode,
        "image_url": f"https://example.com/image_{hash(prompt) % 10000}.png",
        "prompt": prompt,
        "quality": quality,
        "aspect_ratio": aspect_ratio,
    })


@Tool(
    name="generate_tts",
    description="Convert text to speech audio.",
)
async def generate_tts(
    text: Annotated[str, Desc("Text to convert to speech")],
    voice: Annotated[
        Literal["Aria", "Roger", "Sarah", "Brian", "Daniel"],
        Desc("Voice to use for speech"),
    ] = "Aria",
    language: Annotated[str | None, Desc("Language code (e.g., 'en', 'de')")] = None,
) -> ToolResult[dict[str, str]]:
    """Generate text-to-speech audio."""
    return ToolResult.ok({
        "audio_url": f"https://example.com/audio_{hash(text) % 10000}.mp3",
        "voice": voice,
        "language": language or "auto",
    })


@Tool(
    name="generate_video",
    description="Generate video from text or images.",
)
async def generate_video(
    prompt: Annotated[str, Desc("Video generation prompt")],
    image_url: Annotated[str | None, Desc("Image to animate")] = None,
    video_url: Annotated[str | None, Desc("Video to extend")] = None,
    fast: Annotated[bool, Desc("Fast mode (lower quality)")] = True,
    aspect_ratio: Annotated[
        Literal["auto", "16:9", "9:16"], Desc("Output aspect ratio")
    ] = "auto",
) -> ToolResult[dict[str, str]]:
    """Generate a video."""
    if image_url:
        mode = "animate"
    elif video_url:
        mode = "extend"
    else:
        mode = "text-to-video"

    return ToolResult.ok({
        "mode": mode,
        "video_url": f"https://example.com/video_{hash(prompt) % 10000}.mp4",
        "prompt": prompt,
        "quality": "fast" if fast else "high",
    })


@Tool(
    name="generate_music",
    description="Generate music from a text prompt.",
)
async def generate_music(
    prompt: Annotated[str, Desc("Music style and mood description")],
    duration: Annotated[int, Desc("Duration in seconds"), Min(5), Max(150)] = 90,
) -> ToolResult[dict[str, object]]:
    """Generate music from prompt."""
    return ToolResult.ok({
        "music_url": f"https://example.com/music_{hash(prompt) % 10000}.mp3",
        "prompt": prompt,
        "duration": duration,
    })


@Tool(
    name="present_options",
    description="Show clickable options to the user.",
)
async def present_options(
    options: Annotated[list[str], Desc("List of option labels (2-5 items)")],
) -> ToolResult[dict[str, list[str]]]:
    """Present options to the user."""
    return ToolResult.ok({"options_displayed": options})


# Default configuration for direct playground use
configuration = AgentConfig(
    system_prompt="""You are a creative AI assistant for media generation.

You can generate images, audio, video, and music.
After generating media, present relevant follow-up options to the user.""",
    tools=[
        generate_image,
        generate_tts,
        generate_video,
        generate_music,
        present_options,
    ],
    provider="groq",
)