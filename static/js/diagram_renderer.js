/* Shared Mermaid renderer for SummarAIzer
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
                // Use default light theme plus explicit palette to ensure colored nodes
                theme: "default",
                themeVariables: {
                    fontFamily: "Arial, sans-serif",
                    background: "#ffffff",
                    textColor: "#1f2937",
                    primaryTextColor: "#1f2937",
                    secondaryTextColor: "#1f2937",
                    tertiaryTextColor: "#1f2937",
                    lineColor: "#334155",
                    nodeTextColor: "#1f2937",
                    // Node fills (cycled for different node styles)
                    primaryColor: "#fff8e6",   // light blue
                    secondaryColor: "#FFF2CC", // light yellow
                    tertiaryColor: "#E6FFEE",  // light green
                    primaryBorderColor: "#f49631",
                    secondaryBorderColor: "#FDE68A",
                    tertiaryBorderColor: "#A7F3D0",
                    // Provide a categorical palette for diagrams using color scales (e.g., mindmap)
                    cScale0: "#fff8e6",
                    cScale1: "#E6FFEE",
                    cScale2: "#FFF2CC",
                    cScale3: "#FDE1E8",
                    cScale4: "#EDE9FE",
                    cScale5: "#D1FAE5",
                    cScale6: "#FFE4E6",
                    cScale7: "#F0F9FF",
                    // Clusters/notes
                    clusterBkg: "#F8FAFC",
                    clusterBorder: "#94A3B8",
                    noteBkgColor: "#FFEFD5",
                    noteTextColor: "#1f2937",
                    edgeLabelBackground: "#ffffff",
                },
                // Override problematic inline styles that make nodes dark in v11
                themeCSS: `
                    /* General node containers */
                    g.node rect,
                    g.node path,
                    g.node polygon {
                        fill: #fff8e6 !important;
                        stroke: #f49631 !important;
                    }
                    g.node text { fill: #1f2937 !important; }

                    /* Mindmap specific (varies by plugin markup) */
                    g.mindmap-node > path,
                    g.mindmap-node > rect,
                    g[class*="mindmap"] > path,
                    g[class*="mindmap"] > rect {
                        fill: #fff8e6 !important;
                        stroke: #f49631 !important;
                    }
                    g[class*="mindmap"] text { fill: #1f2937 !important; }

                    /* Edges (paths/links) — force visible stroke */
                    g.edgePath path,
                    path.edgePath,
                    g.edgePaths path,
                    g.edge path,
                    path.edge,
                    path.link,
                    .link path {
                        stroke: #334155 !important;
                        stroke-width: 1.5px !important;
                        fill: none !important;
                    }
                    /* Edge labels */
                    .edgeLabel rect { fill: #ffffff !important; stroke: #e5e7eb !important; }
                    .edgeLabel text { fill: #1f2937 !important; }
                    /* Arrowheads */
                    path.arrowheadPath,
                    marker path {
                        fill: #334155 !important;
                        stroke: #334155 !important;
                    }
                `,
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
                        // Fix node colors if Mermaid emitted dark inline styles
                        try { applyNodeColorFix(svg); } catch (e) { /* ignore */ }
                        // Ensure edges are visible
                        try { applyEdgeFix(svg); } catch (e) { /* ignore */ }
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
            console.error("Mermaid render failed", _);
        }
    }

    function applyNodeColorFix(svg) {
        // Light color palette for variety
        const fills = [
            "#fff8e6", // light blue
            "#E6FFEE", // light green
            "#FFF2CC", // light yellow
            "#FDE1E8", // light pink
            "#EDE9FE", // light purple
            "#D1FAE5", // mint
            "#FFE4E6", // rose
            "#F0F9FF", // sky
        ];
        const strokes = [
            "#f49631",
            "#A7F3D0",
            "#FDE68A",
            "#F9A8D4",
            "#C4B5FD",
            "#6EE7B7",
            "#FDA4AF",
            "#7DD3FC",
        ];

        // Candidate selectors for nodes in different diagrams
        const nodeSelectors = [
            'g.node rect',
            'g.node path',
            'g.node polygon',
            'g[class*="mindmap"] > path',
            'g[class*="mindmap"] > rect',
            'g.cluster rect',
        ];
        const textSelectors = [
            'g.node text',
            'g[class*="mindmap"] text',
        ];

        const nodes = svg.querySelectorAll(nodeSelectors.join(','));
        let i = 0;
        nodes.forEach((el) => {
            const idx = i++ % fills.length;
            // Remove Mermaid inline attributes if present
            el.removeAttribute('fill');
            el.removeAttribute('stroke');
            // Force our palette
            el.style.fill = fills[idx];
            el.style.stroke = strokes[idx];
        });

        const texts = svg.querySelectorAll(textSelectors.join(','));
        texts.forEach((t) => {
            t.removeAttribute('fill');
            t.style.fill = '#1f2937';
        });
    }

    function applyEdgeFix(svg) {
        const edgeSelectors = [
            'g.edgePath path',
            'path.edgePath',
            'g.edgePaths path',
            'g.edge path',
            'path.edge',
            'path.link',
            '.link path',
            'marker path',
            'path.arrowheadPath',
        ];
        const edges = svg.querySelectorAll(edgeSelectors.join(','));
        edges.forEach((p) => {
            const sw = parseFloat(p.getAttribute('stroke-width') || p.style.strokeWidth || '1');
            if (!isFinite(sw) || sw <= 0) {
                p.setAttribute('stroke-width', '1.5');
                p.style.strokeWidth = '1.5px';
            }
            const fill = p.getAttribute('fill') || p.style.fill || '';
            if (fill && fill.toLowerCase() !== 'none' && p.matches('g.edgePath path, path.edgePath, g.edge path, path.edge, path.link, .link path')) {
                p.setAttribute('fill', 'none');
                p.style.fill = 'none';
            }
            // Ensure stroke color exists
            const stroke = p.getAttribute('stroke') || p.style.stroke || '';
            if (!stroke) {
                p.setAttribute('stroke', '#334155');
                p.style.stroke = '#334155';
            }
        });
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
