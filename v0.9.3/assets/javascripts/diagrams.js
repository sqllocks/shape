/* Render the bundled Mermaid diagrams and redraw them when the palette changes. */
(() => {
  const sources = new WeakMap();
  let pending = false;
  let running = false;
  async function draw() {
    if (running) { pending = true; return; }
    running = true;
    try {
      const colors = getComputedStyle(document.body);
      const color = name => colors.getPropertyValue(name).trim();
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        themeVariables: {
          darkMode: document.body.dataset.mdColorScheme === "slate",
          fontFamily: "Host Grotesk",
          background: color("--md-default-bg-color"),
          primaryColor: color("--md-code-bg-color"),
          primaryTextColor: color("--md-default-fg-color"),
          primaryBorderColor: color("--md-typeset-a-color"),
          lineColor: color("--md-default-fg-color--light"),
          secondaryColor: color("--md-code-bg-color"),
          tertiaryColor: color("--md-default-bg-color")
        }
      });
      const blocks = document.querySelectorAll("pre.shape-mermaid");
      for (const [index, block] of Array.from(blocks).entries()) {
        if (!sources.has(block)) sources.set(block, block.textContent);
        const { svg } = await mermaid.render(`shape-diagram-${index}`, sources.get(block));
        block.innerHTML = svg;
        if (sources.get(block).includes("A[Your data] --> P[Profile]")) {
          block.classList.add("shape-home-flow");
          block.style.setProperty("--shape-flow-width", `${block.querySelector("svg").viewBox.baseVal.width}px`);
          block.tabIndex = 0;
          block.setAttribute("role", "region");
          block.setAttribute("aria-label", "Profile, check, diff and generate workflow");
        }
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
