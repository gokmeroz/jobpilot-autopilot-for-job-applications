#!/usr/bin/env python
"""
Debug: load Twilio's Greenhouse form and dump every label + element type
so we know exactly what the form contains.  No filling, no submitting.
"""
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")

from playwright.sync_api import sync_playwright

URL = "https://job-boards.greenhouse.io/twilio/jobs/7681338"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(user_agent=(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ))
    page = ctx.new_page()
    page.goto(URL, timeout=30_000, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    page.wait_for_selector("#first_name", timeout=15_000)

    print("\n" + "="*70)
    print("TWILIO GREENHOUSE FORM — all labels and elements")
    print("="*70)

    # 1. All label[for] → element type + options
    print("\n--- ALL label[for] elements ---")
    for label_el in page.query_selector_all("label[for]"):
        for_id = label_el.get_attribute("for") or ""
        label_text = label_el.inner_text().strip().replace("\n", " ")
        el = None
        if for_id and "[" not in for_id:
            try:
                # CSS id selectors can't start with a digit — use attribute selector
                el = page.query_selector(f"[id='{for_id}']")
            except Exception:
                pass
        if el:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            el_type = el.get_attribute("type") or ""
            el_class = (el.get_attribute("class") or "")[:60]
            el_role = el.get_attribute("role") or ""
            required = el.get_attribute("required") or el.get_attribute("aria-required") or ""
            val = ""
            try: val = el.input_value()
            except Exception: pass
            opts = []
            if tag == "select":
                try:
                    raw = el.evaluate("e => Array.from(e.options).map(o => o.text.trim())")
                    opts = [o for o in raw if o]
                except Exception:
                    pass
            print(f"\n  label:    {label_text!r}")
            print(f"  for_id:   {for_id!r}")
            print(f"  tag:      {tag}  type={el_type!r}  role={el_role!r}  required={required!r}")
            print(f"  class:    {el_class!r}")
            if opts:
                print(f"  options:  {opts}")
        else:
            print(f"\n  label:    {label_text!r}  for={for_id!r}  [no element found]")

    # 2. All radio button groups
    print("\n\n--- RADIO BUTTON GROUPS ---")
    radio_names = set()
    for r in page.query_selector_all("input[type='radio']"):
        n = r.get_attribute("name") or ""
        if n not in radio_names:
            radio_names.add(n)
            # find all radios with this name
            radios = page.query_selector_all(f"input[type='radio'][name='{n}']")
            vals = []
            for rb in radios:
                rb_id = rb.get_attribute("id") or ""
                rb_val = rb.get_attribute("value") or ""
                rb_lbl = ""
                if rb_id:
                    lbl = page.query_selector(f"label[for='{rb_id}']")
                    if lbl:
                        rb_lbl = lbl.inner_text().strip()
                vals.append(f"{rb_lbl!r}(value={rb_val!r})")
            print(f"\n  name={n!r}")
            print(f"  options: {vals}")

    # 3. Screenshot full page
    evidence = Path("runs/debug_form")
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / "full_form.png"), full_page=True)
    print(f"\n\nScreenshot saved: {evidence / 'full_form.png'}")

    browser.close()
