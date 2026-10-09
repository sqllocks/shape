/* Render the bundled Mermaid diagrams and redraw them when the palette changes. */
(() => {
  const sources = new WeakMap();
  let pending = false;
  let running = false;
  async function draw() {
    if (running) { pending = true; return; }
    running = true;
    try {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: document.body.dataset.mdColorScheme === "slate" ? "dark" : "default"
      });
      const blocks = document.querySelectorAll("pre.shape-mermaid");
      for (const [index, block] of Array.from(blocks).entries()) {
        if (!sources.has(block)) sources.set(block, block.textContent);
        const { svg } = await mermaid.render(`shape-diagram-${index}`, sources.get(block));
        block.innerHTML = svg;
      }
    } finally {
      running = false;
      if (pending) { pending = false; draw(); }
    }
  }
  function mount() {
    draw();
    new MutationObserver(draw).observe(document.body, {
      attributes: true, attributeFilter: ["data-md-color-scheme"]
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
