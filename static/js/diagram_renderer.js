/* Shared Mermaid renderer for MooMoot Scribe
   - Ensures mermaid ESM is loaded (via CDN) even from non-module contexts
   - Initializes mermaid
   - Exposes window.renderMermaidDiagrams()
   - Observes DOM for dynamically added .mermaid blocks
*/
(function () {
    const STATE = {
        loading: false,
        loaded: false,
        mermaid: null,
        initialized: false,
    };

    function injectModuleLoader() {
        return new Promise((resolve) => {
            if (STATE.loaded && STATE.mermaid) return resolve(STATE.mermaid);
            if (STATE.loading) {
                const onLoaded = () => resolve(STATE.mermaid);
                window.addEventListener("mermaid:loaded", onLoaded, { once: true });
                return;
            }
            STATE.loading = true;
            const s = document.createElement("script");
            s.type = "module";
            s.textContent = `
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
            window.__MMS_mermaid = mermaid;
            window.dispatchEvent(new CustomEvent('mermaid:loaded'));
            `;
            const onLoaded = () => {
                STATE.mermaid = window.__MMS_mermaid || window.mermaid || null;
                STATE.loaded = !!STATE.mermaid;
                resolve(STATE.mermaid);
            };
            window.addEventListener("mermaid:loaded", onLoaded, { once: true });
            document.head.appendChild(s);
        });
    }

    function initMermaid(m) {
        if (!m || STATE.initialized) return;
        try {
            m.initialize({
                startOnLoad: false,
                theme: "default",
                themeVariables: { fontFamily: "Arial, sans-serif" },
                mindmap: { maxNodeSizeX: 200, maxNodeSizeY: 100 },
                flowchart: { useMaxWidth: true, htmlLabels: true },
            });
            STATE.initialized = true;
        } catch (e) {
            console.warn("Mermaid init failed", e);
        }
    }

    async function renderMermaidDiagrams() {
        try {
            const m = STATE.mermaid || (await injectModuleLoader());
            if (!m || typeof m.render !== "function") {
                console.warn("Mermaid not loaded or render function missing");
                return;
            }
            initMermaid(m);

            const mermaidElements = document.querySelectorAll(
                ".mermaid:not([data-processed])"
            );

            if (!mermaidElements.length) {
                return;
            }

            for (const element of mermaidElements) {
                try {
                    const graphDefinition = element.textContent || element.innerText || "";
                    if (!graphDefinition.trim()) {
                        element.setAttribute("data-processed", "true");
                        continue;
                    }
                    element.innerHTML = ""; // clear
                    const id = "mermaid-" + Math.random().toString(36).slice(2, 9);
                    element.id = element.id || id;
                    const out = await m.render(id + "-svg", graphDefinition);
                    element.innerHTML = out.svg || "";
                    element.setAttribute("data-processed", "true");
                    const svg = element.querySelector("svg");
                    if (svg) {
                        svg.style.maxWidth = "100%";
                        svg.style.height = "auto";
                        svg.removeAttribute("width");
                        svg.removeAttribute("height");
                    }
                } catch (err) {
                    element.innerHTML =
                        '<div style="color:#c00;border:1px solid #f99;padding:10px;border-radius:5px;background:#ffe6e6;"><strong>Mermaid Error:</strong> ' +
                        (err && err.message ? err.message : String(err)) +
                        "</div>";
                    element.setAttribute("data-processed", "true");
                    console.error("Mermaid render error", err);
                }
            }
        } catch (_) {
            // ignore
            cachest.error("Mermaid render failed", _);
        }
    }

    // Expose
    window.renderMermaidDiagrams = renderMermaidDiagrams;

    // Observe DOM changes
    const startObserver = () => {
        try {
            const observer = new MutationObserver(() => renderMermaidDiagrams());
            observer.observe(document.body, { childList: true, subtree: true });
        } catch (_) { }
    };

    // Initial hooks
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            renderMermaidDiagrams();
            startObserver();
            setTimeout(renderMermaidDiagrams, 1000);
            setTimeout(renderMermaidDiagrams, 3000);
        });
    } else {
        renderMermaidDiagrams();
        startObserver();
        setTimeout(renderMermaidDiagrams, 1000);
        setTimeout(renderMermaidDiagrams, 3000);
    }
})();
