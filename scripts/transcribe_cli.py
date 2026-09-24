"""Δοκιμή μεταγραφής από τη γραμμή εντολών: python -m scripts.transcribe_cli out_dir img1 img2 ..."""
import asyncio, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.providers import get_provider
from app.transcribe import transcribe_many

async def main(out, imgs):
    s = Settings.load()
    p = get_provider(s)
    t = time.time()
    def done(jid, res, err):
        print(f"{time.time()-t:6.1f}s {jid}: {'OK' if res else 'ERR ' + err}", flush=True)
    res = await transcribe_many(p, [(Path(i).stem, Path(i).read_bytes()) for i in imgs], max_parallel=5, on_done=done)
    out.mkdir(parents=True, exist_ok=True)
    for k, v in res.items():
        (out / f"{k}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")

asyncio.run(main(Path(sys.argv[1]), sys.argv[2:]))
