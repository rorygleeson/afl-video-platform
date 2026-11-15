"""Stage 6: stitch three previously generated REAL MP4s without calling Google.

Invoked by a dedicated Step Functions execution after the API has reserved
assembly status. Binary ffmpeg and ffprobe must be bundled in /opt/bin.
"""
import json
import logging
import os
import re
import subprocess
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
S3 = boto3.client("s3")
DDB = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
BUCKET = os.environ["UPLOAD_BUCKET"]
JOB_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SCENES = ("scene1", "scene2", "scene3")
MAX_INPUT_BYTES = 75 * 1024 * 1024
MAX_OUTPUT_BYTES = 250 * 1024 * 1024
FFMPEG = os.environ.get("FFMPEG_PATH", "/opt/bin/ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH", "/opt/bin/ffprobe")
GREETING_FONT = os.environ.get("GREETING_FONT_PATH", "/var/task/DejaVuSans.ttf")


class AssemblyError(RuntimeError):
    pass


def run(*args, timeout=180):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        # Never leak arbitrary binary/tool stderr into public job state.
        LOG.warning("Media tool failed, exit code %d", result.returncode)
        raise AssemblyError("MEDIA_PROCESSING_FAILED")
    return result.stdout


def inspect(path):
    metadata = json.loads(run(FFPROBE, "-v", "error", "-show_streams",
                              "-show_format", "-of", "json", str(path), timeout=15))
    streams = metadata.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video or video.get("width", 0) < 100 or video.get("height", 0) < 100:
        raise AssemblyError("INVALID_VIDEO_STREAM")
    if float(metadata.get("format", {}).get("duration", 0)) <= 0:
        raise AssemblyError("INVALID_VIDEO_DURATION")
    return {
        "duration": float(metadata["format"]["duration"]),
        "width": video["width"],
        "height": video["height"],
        "codec": video.get("codec_name"),
        "pix_fmt": video.get("pix_fmt"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def set_status(job_id, old, new, **extras):
    names = {"#a": "assembly", "#s": "status"}
    values = {":old": old, ":new": new, ":now": datetime.now(timezone.utc).isoformat()}
    updates = ["#a.#s = :new", "#a.updatedAt = :now"]
    for index, (key, value) in enumerate(extras.items()):
        placeholder = ":v" + str(index)
        values[placeholder] = value
        updates.append("#a." + key + " = " + placeholder)
    DDB.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET " + ", ".join(updates),
        ConditionExpression="#a.#s = :old",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def render_personal_message(source, destination, message, duration, directory):
    """Pillow renders text into PNG; existing FFmpeg overlays and holds it.

    This works with the deployed FFmpeg static build, which has overlay/tpad
    but lacks drawtext. User text NEVER enters FFmpeg filter arguments.
    """
    if not isinstance(message, str) or len(message) > 80 or any(
            ord(ch) < 32 or ord(ch) == 127 for ch in message):
        raise AssemblyError("INVALID_PERSONAL_MESSAGE")
    font_path = Path(GREETING_FONT)
    if not font_path.is_file():
        raise AssemblyError("GREETING_FONT_UNAVAILABLE")
    lines = textwrap.wrap(message, width=28, break_long_words=True,
                          break_on_hyphens=False)
    if not lines or len(lines) > 3:
        raise AssemblyError("INVALID_PERSONAL_MESSAGE")

    from PIL import Image, ImageDraw, ImageFont

    overlay_path = directory / "greeting.png"
    card = Image.new("RGBA", (720, 1280), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((24, 1040, 696, 1235), radius=18,
                           fill=(0, 0, 0, 188))
    font = ImageFont.truetype(str(font_path), size=35)
    text = "\n".join(lines)
    bounds = draw.multiline_textbbox((0, 0), text, font=font,
                                     spacing=9, align="center")
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    if width > 630 or height > 170:
        raise AssemblyError("GREETING_LAYOUT_TOO_LARGE")
    draw.multiline_text(((720 - width) / 2 - bounds[0],
                         1040 + (195 - height) / 2 - bounds[1]),
                        text, font=font, fill=(255, 255, 255, 255),
                        spacing=9, align="center")
    card.save(overlay_path, format="PNG")

    start = max(0, duration - 0.5)
    # Fade the transparent PNG in during Scene 3's last half-second.
    # tpad clones the final frame for two additional seconds.
    filter_graph = (
        "[0:v]tpad=stop_mode=clone:stop_duration=2[base];"
        f"[1:v]format=rgba,fade=t=in:st={start:.3f}:d=0.5:alpha=1[card];"
        "[base][card]overlay=0:0:shortest=1:format=auto[v]"
    )
    run(FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-loop", "1", "-framerate", "30",
        "-i", str(overlay_path), "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "0:a:0?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-crf", "22", "-c:a", "copy", "-t", f"{duration + 2:.3f}",
        "-movflags", "+faststart", str(destination), timeout=180)


def assemble(job_id):
    if not isinstance(job_id, str) or not JOB_PATTERN.fullmatch(job_id):
        raise AssemblyError("INVALID_JOB_ID")
    item = DDB.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item")
    if not item or item.get("assembly", {}).get("status") != "QUEUED":
        raise AssemblyError("ASSEMBLY_NOT_QUEUED")
    # Verify the three *real* completed videos, not mock video results.
    for scene in SCENES:
        video = item.get("realVideos", {}).get(scene, {})
        if video.get("status") != "COMPLETED" or video.get("resultKey") != f"videos/{job_id}/{scene}.mp4":
            raise AssemblyError("SOURCE_VIDEO_NOT_READY")
    set_status(job_id, "QUEUED", "PROCESSING")
    try:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            sources = []
            for scene in SCENES:
                key = f"videos/{job_id}/{scene}.mp4"
                head = S3.head_object(Bucket=BUCKET, Key=key)
                if not 128 <= head.get("ContentLength", 0) <= MAX_INPUT_BYTES:
                    raise AssemblyError("SOURCE_VIDEO_SIZE_INVALID")
                dest = root / (scene + ".mp4")
                S3.download_file(BUCKET, key, str(dest))
                sources.append(dest)
            info = [inspect(p) for p in sources]
            # Normalize the source dimensions/frame-rate/audio for a reliably
            # playable joined MP4, even if Google gives clips different codecs.
            normalized = []
            for i, source in enumerate(sources):
                target = root / ("normalized-" + str(i) + ".mp4")
                args = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-y", "-i", str(source)]
                if not info[i]["has_audio"]:
                    args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                args += ["-map", "0:v:0", "-map", "0:a:0" if info[i]["has_audio"] else "1:a:0",
                         "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
                                "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
                         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                         "-crf", "22", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                         "-shortest", "-movflags", "+faststart", str(target)]
                run(*args, timeout=150)
                inspect(target)
                normalized.append(target)
            manifest = root / "concat.txt"
            manifest.write_text("".join(f"file '{p.name}'\n" for p in normalized))
            final = root / "final.mp4"
            run(FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(manifest),
                "-c", "copy", "-movflags", "+faststart", str(final), timeout=60)
            final_info = inspect(final)
            message = item.get("personalMessage", "")
            if message:
                overlaid = root / "final-with-message.mp4"
                render_personal_message(final, overlaid, message, final_info["duration"], root)
                overlaid_info = inspect(overlaid)
                if overlaid_info["duration"] < final_info["duration"] + 1.7:
                    raise AssemblyError("GREETING_HOLD_MISSING")
                final = overlaid
            size = final.stat().st_size
            if not 128 <= size <= MAX_OUTPUT_BYTES:
                raise AssemblyError("FINAL_VIDEO_SIZE_INVALID")
            key = f"videos/{job_id}/final.mp4"
            S3.upload_file(str(final), BUCKET, key, ExtraArgs={
                "ContentType": "video/mp4", "ServerSideEncryption": "AES256"})
            S3.head_object(Bucket=BUCKET, Key=key)
            set_status(job_id, "PROCESSING", "COMPLETED", resultKey=key, sizeBytes=size)
            return {"status": "COMPLETED", "jobId": job_id}
    except Exception as exc:
        code = str(exc) if isinstance(exc, AssemblyError) else "ASSEMBLY_PROCESSING_ERROR"
        try:
            set_status(job_id, "PROCESSING", "FAILED", errorCode=code)
        except ClientError:
            LOG.exception("Could not mark assembly failure")
        LOG.exception("Assembly failed for job")
        raise


def lambda_handler(event, context):
    if event.get("op") == "health":
        return {"status": "READY" if os.access(FFMPEG, os.X_OK)
                and os.access(FFPROBE, os.X_OK) else "NOT_CONFIGURED",
                "videoGeneration": False}
    return assemble(event.get("jobId"))
