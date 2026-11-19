// Run in the YouTube Studio tab (built-in browser) AFTER "Upload videos" is open.
// Puts clips/<FILE> into the upload box with no file picker: serve_clips.py (127.0.0.1:8766)
// hands the bytes over via bridge.html (popup, or hidden iframe if popups are blocked).
// Then poll window.__clip ("waiting" -> "sent <name> <bytes>" or "error ...").
(() => {
  const FILE = "__FILE__";  // e.g. kaicenat-gta6_2026-11-19/kaicenat-gta6_01.mp4
  window.__clip = "waiting";
  const put = (d) => {
    const input = document.querySelector('ytcp-uploads-file-picker input[type=file]') ||
                  document.querySelector('input[type=file]');
    if (!input) { window.__clip = "error: no upload input (open Upload videos first)"; return; }
    const dt = new DataTransfer();
    dt.items.add(new File([d.buf], d.name, {type: "video/mp4"}));
    input.files = dt.files;
    input.dispatchEvent(new Event("change", {bubbles: true}));
    window.__clip = "sent " + d.name + " " + d.buf.byteLength;
  };
  const h = (e) => {
    if (e.origin !== "http://127.0.0.1:8766" || !e.data || !e.data.clipBridge) return;
    window.removeEventListener("message", h);
    put(e.data);
  };
  window.addEventListener("message", h);
  const url = "http://127.0.0.1:8766/bridge.html?f=" + encodeURIComponent(FILE);
  const w = window.open(url, "clipbridge", "width=240,height=120");
  if (!w) {  // popup blocked -> try a hidden iframe
    const f = document.createElement("iframe");
    f.style.display = "none"; f.src = url; document.body.appendChild(f);
  }
  setTimeout(() => { if (window.__clip === "waiting") window.__clip = "error: bridge timeout (is serve_clips.py running? CSP?)"; }, 90000);
  return "started " + FILE;
})();
