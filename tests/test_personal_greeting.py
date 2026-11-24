"""Offline greeting pipeline contract tests; never invoke AWS or paid models."""
import ast
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "src/api/handler.py"
WORKER = ROOT / "src/assembly_worker/handler.py"
FRONT = ROOT / "frontend/app.js"
HTML = ROOT / "frontend/index.html"


def isolated_function(path, name, globals_):
    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    code = compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec")
    exec(code, globals_)
    return globals_[name]


class GreetingTests(unittest.TestCase):
    def test_api_accepts_optional_and_rejects_bad_messages(self):
        check = isolated_function(API, "validate_personal_message", {})
        self.assertEqual(check(None), "")
        self.assertEqual(check(" Happy Birthday Dad "), "Happy Birthday Dad")
        self.assertEqual(check("a"*80), "a"*80)
        for invalid in (42, "a"*81, "Hi\nDad", "Bad\rMsg", "Bad\x00Msg"):
            with self.assertRaises(ValueError):
                check(invalid)
        source = API.read_text()
        self.assertIn('"personalMessage": personal_message', source)
        self.assertIn('validate_personal_message(', source)

    def test_worker_overlay_keeps_video_source_and_not_paid_models(self):
        source = WORKER.read_text()
        ast.parse(source)
        self.assertIn('message = item.get("personalMessage", "")', source)
        self.assertIn('if message:', source)
        self.assertIn('stop_duration=2', source)
        self.assertIn('from PIL import Image, ImageDraw, ImageFont', source)
        self.assertIn('set_status(job_id, "PROCESSING", "COMPLETED"', source)
        self.assertIn('"-c", "copy"', source)
        self.assertNotIn('generativelanguage.googleapis.com', source)

    def test_worker_renders_png_overlay_without_ffmpeg_text_injection(self):
        class AssemblyError(Exception):
            pass
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
            self.assertTrue(font.exists())
            globals_ = {
                "Path": Path, "textwrap": textwrap, "AssemblyError": AssemblyError,
                "GREETING_FONT": str(font), "FFMPEG": "ffmpeg",
                "run": lambda *args, **kwargs: calls.append(args)
            }
            render = isolated_function(WORKER, "render_personal_message", globals_)
            message = "Happy Birthday Dad, from Mary"
            render(Path(directory)/"original.mp4",Path(directory)/"output.mp4",
                   message, 17.0,Path(directory))
            args = calls[0]
            vf = args[args.index("-filter_complex") + 1]
            self.assertIn("stop_duration=2", vf)
            self.assertIn("overlay=0:0", vf)
            self.assertNotIn(message, vf)
            self.assertTrue((Path(directory)/"greeting.png").is_file())
            from PIL import Image
            with Image.open(Path(directory)/"greeting.png") as card:
                self.assertEqual(card.size, (720,1280))
                self.assertGreater(card.getpixel((30,1100))[3], 0)
            with self.assertRaises(AssemblyError):
                render("a","b","x"*81,3,Path(directory))


    def test_optional_frontend_field(self):
        html=HTML.read_text()
        script=FRONT.read_text()
        self.assertIn('id="personalMessage" maxlength="80"',html)
        self.assertIn('Happy Birthday Dad, from Mary',html)
        self.assertIn("personalMessage: document.getElementById('personalMessage').value.trim()",script)


if __name__ == "__main__":
    unittest.main()
