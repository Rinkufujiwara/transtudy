"""Layout acceptance check for Transtudy (fake server). Usage: python layout_check.py <port> <wav> <outdir>"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT, WAV, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3])
SILENT = sys.argv[4] if len(sys.argv) > 4 else None
URL = f"http://localhost:{PORT}"
SIZES = [(1280, 760), (1366, 768), (1062, 600)]
results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def rect(page, sel):
    return page.evaluate(f"(() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect(); return {{x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom,r:r.right}}; }})()")


def run(p, size, wav, tag):
    w, h = size
    b = p.chromium.launch(channel="msedge", args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                                   f"--use-file-for-fake-audio-capture={wav}%noloop"])
    page = b.new_page(viewport={"width": w, "height": h})
    page.goto(URL)
    page.wait_for_selector(".hcard")
    time.sleep(0.8)
    return b, page


with sync_playwright() as p:
    for size in SIZES:
        tag = f"{size[0]}x{size[1]}"
        b, page = run(p, size, WAV, tag)
        fr = rect(page, "#frame")
        ok(f"{tag} frame 9:16", abs(fr["w"] / fr["h"] - 9 / 16) < 0.01, f"{fr['w']:.0f}x{fr['h']:.0f}")
        ok(f"{tag} frame visible", fr["x"] >= 0 and fr["y"] >= 0 and fr["r"] <= size[0] and fr["b"] <= size[1])
        ok(f"{tag} page no scroll", page.evaluate("document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth <= innerWidth"))
        mic_idle = rect(page, "#mic")
        tab = rect(page, "#tabbar")
        inp = rect(page, "#inputRow")
        ok(f"{tag} input above tab bar", inp["b"] <= tab["y"])
        ok(f"{tag} home no scroll", page.evaluate("(() => { const h = document.getElementById('home'); return h.scrollHeight <= h.clientHeight; })()"))
        word = page.evaluate("parseFloat(getComputedStyle(document.querySelector('.hcard-word')).fontSize)")
        if size[1] == 600:
            ok(f"{tag} review word scaled < 42px", word < 42, f"{word}px")
        nxt = page.inner_text(".hcard-next")
        cnt = page.inner_text(".strip-n")
        n_words = int(cnt.split()[0])
        shown = [s for s in nxt.split(" · ") if "more" not in s]
        more = sum(int(s.split()[0]) for s in nxt.split(" · ") if "more" in s)
        ok(f"{tag} N more adds up", 1 + len(shown) + more == n_words, f"{nxt} / {cnt}")
        # carousel: wheel + drag
        car = rect(page, "#carousel")
        x0 = page.evaluate("document.getElementById('carousel').scrollLeft")
        page.mouse.move(car["x"] + car["w"] / 2, car["y"] + car["h"] / 2)
        page.mouse.wheel(0, 120)
        time.sleep(0.9)
        x1 = page.evaluate("document.getElementById('carousel').scrollLeft")
        cards = page.evaluate("document.querySelectorAll('.hcard').length")
        if cards > 1:
            ok(f"{tag} wheel moves carousel", x1 > x0 + 50, f"{x0}->{x1}")
            cx = car["x"] + car["w"] / 2
            page.mouse.move(cx, car["y"] + car["h"] / 2)
            page.mouse.down()
            for i in range(1, 11):
                page.mouse.move(cx + i * 20, car["y"] + car["h"] / 2)
            page.mouse.up()
            time.sleep(0.9)
            x2 = page.evaluate("document.getElementById('carousel').scrollLeft")
            sheet_open = page.evaluate("document.getElementById('cardSheet').classList.contains('open')")
            ok(f"{tag} drag moves back, no lesson", x2 < x1 - 50 and not sheet_open, f"{x1}->{x2}")
        else:
            ok(f"{tag} carousel has recent cards", False, "only 1 card")
        page.screenshot(path=str(OUT / f"{tag}-idle.png"))
        # listening (fake mic)
        page.click("#mic")
        page.wait_for_selector('#home[data-state="listening"]')
        time.sleep(1.6)
        mic_l = rect(page, "#mic")
        ok(f"{tag} mic same place/size listening", abs(mic_l["y"] - mic_idle["y"]) < 1 and abs(mic_l["w"] - 96) < 1)
        line = rect(page, "#micLine")
        ring = rect(page, ".ring.r2")
        ok(f"{tag} listening line below ring & above tabbar", line["y"] >= ring["b"] - 1 and line["b"] <= tab["y"], f"{line['y']:.0f} ring {ring['b']:.0f} tab {tab['y']:.0f}")
        page.screenshot(path=str(OUT / f"{tag}-listening.png"))
        page.click("#mic")
        page.wait_for_selector('#home[data-state="result"]', timeout=15000)
        tr_before = rect(page, "#trans")
        page.wait_for_selector(".tag", timeout=15000)
        time.sleep(0.4)
        tr_after = rect(page, "#trans")
        ok(f"{tag} translation does not move when tags arrive", abs(tr_before["y"] - tr_after["y"]) < 1)
        mic_r = rect(page, "#mic")
        ok(f"{tag} mic same place result", abs(mic_r["y"] - mic_idle["y"]) < 1)
        ok(f"{tag} live result is receipt->recibo", page.inner_text("#trans").strip().lower() == "recibo", page.inner_text("#trans"))
        page.screenshot(path=str(OUT / f"{tag}-result.png"))
        # long text
        page.click("#btnClose")
        page.wait_for_selector('#home[data-state="idle"]')
        long = ("The quick brown fox jumps over the lazy dog near the river bank while the children play and " * 4)[:299]
        page.fill("#textIn", long)
        page.keyboard.press("Enter")
        page.wait_for_selector(".tag", timeout=15000)
        time.sleep(0.4)
        res = rect(page, "#result")
        mic_r = rect(page, "#mic")
        kids_bottom = page.evaluate("Math.max(...[...document.getElementById('result').children].filter(c=>c.offsetParent).map(c=>c.getBoundingClientRect().bottom))")
        ok(f"{tag} 300-char text fits above mic", kids_bottom <= mic_r["y"] and res["b"] <= mic_r["y"], f"{kids_bottom:.0f} vs mic {mic_r['y']:.0f}")
        tags_fit = page.evaluate("(() => { const t = document.getElementById('tags'); return t.scrollWidth <= t.clientWidth + 1; })()")
        ok(f"{tag} tags not cut", tags_fit)
        page.screenshot(path=str(OUT / f"{tag}-long.png"))
        # tab key never scrolls frame sideways
        for _ in range(40):
            page.keyboard.press("Tab")
        sx = page.evaluate("(() => { const f = document.getElementById('frame'); return f.scrollLeft + document.getElementById('screens').scrollLeft; })()")
        ok(f"{tag} no sideways scroll after Tab", sx == 0, str(sx))
        b.close()

    # silent mic
    if SILENT:
        b, page = run(p, (1366, 768), SILENT, "silent")
        page.click("#mic")
        time.sleep(1.5)
        page.click("#mic")
        time.sleep(0.8)
        txt = page.inner_text("#micLine")
        ok("silent mic shows No sound, nothing sent", "No sound" in txt and page.evaluate("document.getElementById('home').dataset.state") == "idle", txt)
        b.close()

    # wordlist + settings + book on 1366
    b, page = run(p, (1366, 768), WAV, "misc")
    page.click('[data-tab="wordlist"]')
    page.wait_for_selector("#wlList .row")
    tg = page.evaluate("(() => { const t = [...document.querySelectorAll('#wlToggles .tg')].pop().getBoundingClientRect(); const l = document.querySelector('#wlList .row').getBoundingClientRect(); return l.top - t.bottom; })()")
    ok("wordlist gap between toggles and list", tg >= 12, f"{tg}")
    sep = page.evaluate("(() => { const r = document.querySelector('#wlList .row-text').getBoundingClientRect(); const l = document.getElementById('wlList').getBoundingClientRect(); return Math.abs(r.right - l.right); })()")
    ok("wordlist separators reach right edge", sep < 1, f"{sep}")
    page.click('[data-tab="wordbook"]')
    page.wait_for_selector(".shelf-card")
    page.click(".b-open >> nth=0")
    page.wait_for_selector("#book.open")
    time.sleep(1.2)
    page.click("#bookBack")
    time.sleep(0.5)
    page.click('[data-tab="home"]')
    page.click("#btnMenu")
    time.sleep(0.1)
    tr0 = page.evaluate("getComputedStyle(document.getElementById('settingsPanel')).transform")
    time.sleep(0.6)
    tr1 = page.evaluate("getComputedStyle(document.getElementById('settingsPanel')).transform")
    ok("settings slides in from the right after a book", tr0 != tr1 and tr1 in ("none", "matrix(1, 0, 0, 1, 0, 0)"), f"{tr0} -> {tr1}")
    gear = page.evaluate("getComputedStyle(document.getElementById('bookGear')).color")
    ok("gear is tab-bar gray", gear == "rgb(138, 138, 142)", gear)
    b.close()

for name, passed, detail in results:
    print(("PASS " if passed else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
print(f"{sum(r[1] for r in results)}/{len(results)} passed")
