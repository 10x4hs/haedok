# ruff: noqa: E501 — 장면 HTML은 한 줄씩 두는 편이 읽기 쉽다
"""게시물 스크린샷용 '내가 찍은 사진' 여섯 장 — 실제 사진 대신 앱의 표지 그림(app/static/covers/showcase)으로
책상 · 밑줄 그은 쪽 · 창가 카페 · 책장 · 침대 · 공원 벤치를 HTML로 그려 JPEG로 저장한다(개인 사진 없음).

    python scripts/readme_photos.py <사진 폴더>      # desk · page · cafe · shelf(4:5) · bed · park(1:1) .jpg

seed_posts_ig.py 가 이 폴더를 읽는다. 따로 받은 크로미움을 쓰려면 CHROME=/path/to/chrome.
"""

import asyncio
import base64
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

COV = Path(__file__).resolve().parent.parent / "app" / "static" / "covers" / "showcase"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "readme-photos")
CHROME = os.environ.get("CHROME") or None


def cov(n):
    return "data:image/jpeg;base64," + base64.b64encode((COV / n).read_bytes()).decode()


BASE = """<style>*{box-sizing:border-box;margin:0}body{width:%dpx;height:%dpx;overflow:hidden;font-family:'Apple SD Gothic Neo','Noto Serif KR',serif}</style>"""
SCENES = {
    # 1. 나무 책상 위 데미안 + 커피 (4:5)
    "desk": (
        1080,
        1350,
        """<div style="position:absolute;inset:0;background:
   repeating-linear-gradient(92deg,rgba(0,0,0,.06) 0 3px,transparent 3px 22px),
   repeating-linear-gradient(88deg,rgba(255,255,255,.04) 0 1px,transparent 1px 9px),
   linear-gradient(160deg,#8a5a36,#6b4226 55%,#4a2c18)"></div>
   <div style="position:absolute;left:170px;top:250px;width:560px;height:840px;transform:rotate(-7deg);box-shadow:30px 50px 70px rgba(0,0,0,.55),4px 0 0 #eee inset;border-radius:4px 10px 10px 4px;background:url(%s) center/cover"></div>
   <div style="position:absolute;right:60px;top:110px;width:330px;height:330px;border-radius:50%%;background:radial-gradient(circle at 50%% 50%%,#2b1a10 0 54%%,#c9a27a 55%% 58%%,#f4efe8 59%% 100%%);box-shadow:20px 34px 50px rgba(0,0,0,.5)"></div>
   <div style="position:absolute;right:120px;top:170px;width:120px;height:60px;border-radius:50%%;background:radial-gradient(ellipse,rgba(255,240,220,.6),transparent 70%%)"></div>
   <div style="position:absolute;left:640px;top:980px;width:380px;height:26px;transform:rotate(-24deg);background:linear-gradient(90deg,#1d1d1f,#3a3a3c 80%%,#c0a060 80%% 86%%,#1d1d1f 86%%);border-radius:13px;box-shadow:10px 18px 24px rgba(0,0,0,.45)"></div>
   <div style="position:absolute;inset:0;background:radial-gradient(120%% 90%% at 30%% 10%%,rgba(255,220,170,.25),transparent 60%%),radial-gradient(90%% 80%% at 80%% 100%%,rgba(0,0,0,.35),transparent)"></div>""",
    ),
    # 2. 밑줄 그은 쪽 (4:5)
    "page": (
        1080,
        1350,
        """<div style="position:absolute;inset:0;background:linear-gradient(170deg,#d9cbb3,#bfae92)"></div>
   <div style="position:absolute;left:70px;top:120px;right:70px;bottom:120px;transform:rotate(2deg);background:linear-gradient(90deg,#f6f0e3 0 48%%,#e6dcc8 49.5%%,#f6f0e3 51%% 100%%);box-shadow:0 40px 80px rgba(60,40,20,.45);border-radius:6px;padding:90px 70px;display:grid;grid-template-columns:1fr 1fr;gap:110px;color:#3a332b;font-size:30px;line-height:1.85">
   <div>나는 오직, 내 속에서 스스로 솟아나오려는 것, 바로 그것을 살아 보려 했다. 왜 그것이 그토록 어려웠을까.<br><br>모든 사람의 삶은 자기 자신에게로 이르는 길이다. 하나의 길의 시도, 하나의 오솔길의 암시다.</div>
   <div><span style="background:linear-gradient(transparent 58%%,rgba(255,170,190,.75) 58%%)">새는 알에서 나오려고 투쟁한다. 알은 세계이다. 태어나려는 자는 하나의 세계를 깨뜨려야 한다.</span><br><br>새는 신에게로 날아간다. 그 신의 이름은 아브락사스.</div></div>
   <div style="position:absolute;right:110px;bottom:60px;width:300px;height:22px;transform:rotate(-18deg);background:linear-gradient(90deg,#ff8fab,#ff8fab 78%%,#333 78%%);border-radius:11px;box-shadow:8px 14px 18px rgba(0,0,0,.3)"></div>
   <div style="position:absolute;inset:0;background:radial-gradient(100%% 80%% at 20%% 0%%,rgba(255,240,200,.3),transparent 60%%)"></div>""",
    ),
    # 3. 창가 카페 (4:5)
    "cafe": (
        1080,
        1350,
        """<div style="position:absolute;inset:0;background:linear-gradient(180deg,#a7c4d8 0%%,#e9d8bf 52%%,#5a3d28 52.2%%,#3e2a1c)"></div>
   <div style="position:absolute;left:0;right:0;top:0;height:700px;background:repeating-linear-gradient(90deg,transparent 0 340px,#2b2b2b 340px 360px)"></div>
   %s
   <div style="position:absolute;left:120px;top:760px;width:420px;height:600px;transform:rotate(12deg);background:url(%s) center/cover;box-shadow:24px 40px 60px rgba(0,0,0,.6);border-radius:4px 8px 8px 4px"></div>
   <div style="position:absolute;right:140px;top:820px;width:300px;height:300px;border-radius:50%%;background:radial-gradient(circle,#efe2cf 0 30%%,#b88a5c 31%% 52%%,#fafafa 53%% 100%%);box-shadow:16px 30px 44px rgba(0,0,0,.5)"></div>
   <div style="position:absolute;inset:0;background:radial-gradient(70%% 50%% at 50%% 30%%,rgba(255,255,255,.18),transparent)"></div>""",
    ),
    # 4. 책장 (4:5)
    "shelf": (
        1080,
        1350,
        """<div style="position:absolute;inset:0;background:#3b2a1f"></div><div id=s style="position:absolute;inset:40px;display:grid;grid-template-rows:repeat(4,1fr);gap:40px"></div>
   <script>const c=['#6b1f2a','#2f4a2b','#c48a2c','#4b2a4f','#384250','#0f5257','#a1461f','#e8e1d1','#1d2a44','#8a6d3b'];const s=document.getElementById('s');for(let r=0;r<4;r++){const row=document.createElement('div');row.style.cssText='display:flex;align-items:flex-end;gap:6px;border-bottom:26px solid #5c4030;box-shadow:0 30px 30px -20px rgba(0,0,0,.6)';for(let i=0;i<20;i++){const b=document.createElement('div');const h=200+((r*7+i*13)%%9)*12;b.style.cssText=`width:${34+((i*5+r)%%4)*8}px;height:${h}px;background:linear-gradient(90deg,rgba(255,255,255,.12),transparent 30%%,rgba(0,0,0,.25)),${c[(i*3+r*5)%%c.length]};border-radius:3px 3px 0 0`;row.append(b)}s.append(row)}</script>
   <div style="position:absolute;inset:0;background:radial-gradient(90%% 70%% at 50%% 30%%,rgba(255,210,150,.22),transparent 70%%)"></div>""",
    ),
    # 5. 정사각: 침대 위 표지 두 권 (1:1)
    "bed": (
        1080,
        1080,
        """<div style="position:absolute;inset:0;background:repeating-linear-gradient(45deg,rgba(0,0,0,.025) 0 2px,transparent 2px 6px),linear-gradient(135deg,#eef0f3,#cfd5dc)"></div>
   <div style="position:absolute;left:150px;top:170px;width:440px;height:660px;transform:rotate(-10deg);background:url(%s) center/cover;box-shadow:24px 36px 50px rgba(30,40,60,.35)"></div>
   <div style="position:absolute;left:500px;top:250px;width:420px;height:630px;transform:rotate(8deg);background:url(%s) center/cover;box-shadow:24px 36px 50px rgba(30,40,60,.35)"></div>
   <div style="position:absolute;inset:0;background:radial-gradient(80%% 60%% at 20%% 10%%,rgba(255,250,240,.5),transparent 60%%)"></div>""",
    ),
    # 6. 정사각: 공원 벤치 위 책 (1:1)
    "park": (
        1080,
        1080,
        """<div style="position:absolute;inset:0;background:radial-gradient(circle at 20%% 20%%,#cfe6a8,transparent 30%%),radial-gradient(circle at 80%% 10%%,#9cc47a,transparent 35%%),linear-gradient(#7fae5a,#4f7d3a)"></div>
   <div style="position:absolute;left:0;right:0;top:520px;height:560px;background:repeating-linear-gradient(0deg,#8b6a4a 0 90px,#5a4230 90px 104px)"></div>
   <div style="position:absolute;left:330px;top:300px;width:430px;height:640px;transform:rotate(4deg);background:url(%s) center/cover;box-shadow:20px 40px 50px rgba(0,0,0,.45)"></div>""",
    ),
}


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bokeh = "".join(
        f'<div style="position:absolute;left:{(i * 137) % 1000}px;top:{(i * 89) % 600}px;width:{60 + (i % 5) * 30}px;height:{60 + (i % 5) * 30}px;border-radius:50%;background:rgba(255,{220 - (i % 3) * 30},160,.35);filter:blur(6px)"></div>'
        for i in range(18)
    )
    fills = {
        "desk": (cov("demian.jpg"),),
        "page": (),
        "cafe": (bokeh, cov("the-stranger.jpg")),
        "shelf": (),
        "bed": (cov("vegetarian.jpg"), cov("the-white-book.jpg")),
        "park": (cov("old-man-sea.jpg"),),
    }
    async with async_playwright() as pw:
        br = await pw.chromium.launch(executable_path=CHROME)
        for name, (w, h, html) in SCENES.items():
            pg = await br.new_page(viewport={"width": w, "height": h})
            parts = html.split("%s")
            body = parts[0]
            for val, rest in zip(fills[name], parts[1:], strict=True):
                body += val + rest
            body = body.replace("%%", "%")
            await pg.set_content(BASE % (w, h) + body)
            await pg.wait_for_timeout(300)
            await pg.screenshot(path=str(OUT / f"{name}.jpg"), type="jpeg", quality=78)
            await pg.close()
        await br.close()
    for f in sorted(OUT.glob("*.jpg")):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    asyncio.run(main())
