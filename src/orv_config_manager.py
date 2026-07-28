import json

FFMPEG_CODEC = "h264_nvenc"
FFMPEG_PRESET = "p7"
FFMPEG_EXEC = "ffmpeg"
FFMPEG_LIBS = ""
FFMPEG_BITRATE = "100M"
FFMPEG_MODE = "Pipe"
FFMPEG_CUDA = False

RENDERER = "Auto"
PIXEL_FORMAT = "YUV420"
YUV_COLOR_SPACE = "BT709"

class Config:
    resolution: str = "1280x720"
    fps: int = 60
    background_dim: float = 0.75
    scroll_speed: float = 26.0
    show_storyboard_or_video: bool = True
    use_beatmap_hitsounds: bool = False
    use_beatmap_skin: bool = False
    use_beatmap_colors: bool = False
    music_volume: float = 0.5
    effects_volume: float = 0.5
    master_volume: float = 0.5
    skip_intro: bool = True

def generate_config(config: Config) -> str:
    config_dict = {
        "record_options": {
            "fps": config.fps,
            "resolution": config.resolution,
            "renderer": RENDERER
        },
        "ffmpeg_options": {
            "mode": FFMPEG_MODE,
            "libraries_path": FFMPEG_LIBS,
            "ffmpeg_executable": FFMPEG_EXEC,
            "video_encoder": FFMPEG_CODEC,
            "video_encoder_preset": FFMPEG_PRESET,
            "video_encoder_bitrate": FFMPEG_BITRATE,
            "use_cuda_if_possible": FFMPEG_CUDA
        },
        "output_options": {
            "pixel_format": PIXEL_FORMAT,
            "yuv_color_space": YUV_COLOR_SPACE
        },
        "game_settings": {
            "skip_intro": config.skip_intro,
            "background_dim": config.background_dim,
            "scroll_speed": config.scroll_speed,
            "show_storyboard_or_video": config.show_storyboard_or_video,
            "use_beatmap_hitsounds": config.use_beatmap_hitsounds,
            "use_beatmap_skin": config.use_beatmap_skin,
            "use_beatmap_colors": config.use_beatmap_colors,
            "music_volume": config.music_volume,
            "effects_volume": config.effects_volume,
            "master_volume": config.master_volume
        }
    }
    return json.dumps(config_dict, indent=2)
