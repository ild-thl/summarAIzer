// Delegated handler for GDPR entity links injected into Gradio HTML
(function () {

    function setSelectedEntity(val) {
        try {
            const sel = document.querySelector('#gdpr_entity_selector input');
            // helper to try to confirm selection for custom widgets (e.g. Gradio)
            function confirmSelection(node, value) {
                try {
                    // common sequence: focus, mouse events, keyboard Enter, blur
                    node.focus && node.focus();
                    ['mousedown', 'mouseup', 'click'].forEach(n => node.dispatchEvent(new MouseEvent(n, { bubbles: true })));
                    node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
                    node.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
                    node.dispatchEvent(new Event('input', { bubbles: true }));
                    node.dispatchEvent(new Event('change', { bubbles: true }));
                    node.blur && node.blur();

                    // try to click an option-like element inside the selector wrapper
                    const wrapper = node.closest ? node.closest('#gdpr_entity_selector') : document.getElementById('gdpr_entity_selector');
                    if (wrapper) {
                        // look for elements that represent options
                        const candidates = wrapper.querySelectorAll('[role="option"], li, button, .gradio-select-option, .option');
                        for (let c of candidates) {
                            try {
                                const txt = (c.textContent || '').trim();
                                const dv = c.getAttribute('data-value') || c.getAttribute('data-value-text') || c.getAttribute('data-value');
                                if (txt === value || dv === value || (c.value && c.value === value)) {
                                    c.click();
                                    c.dispatchEvent(new Event('click', { bubbles: true }));
                                    return true;
                                }
                            } catch (e) { /* ignore */ }
                        }
                    }
                } catch (e) { console.log('confirmSelection error', e); }
                return false;
            }

            if (sel) {
                // If it's a native <select>, iterate options.
                if (sel.tagName === 'SELECT') {
                    for (let i = 0; i < sel.options.length; i++) {
                        if (sel.options[i].value == val) {
                            sel.selectedIndex = i;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            break;
                        }
                    }
                } else {
                    // For input-based listboxes (Gradio), try multiple times to ensure options are rendered and clicked
                    (async () => {
                        const controlsId = sel.getAttribute('aria-controls');
                        const openDropdown = () => { try { sel.click(); } catch (e) { } };
                        let matched = false;
                        const attempts = 5;
                        for (let attempt = 0; attempt < attempts && !matched; attempt++) {
                            try {
                                if (controlsId) {
                                    openDropdown();
                                    const optionsContainer = document.getElementById(controlsId) || document.querySelector('#' + CSS.escape(controlsId));
                                    if (optionsContainer) {
                                        const opts = optionsContainer.querySelectorAll('[role="option"], li, button, [data-option], .gradio-select-option');
                                        for (let o of opts) {
                                            try {
                                                const txt = (o.textContent || '').trim();
                                                const dv = o.getAttribute('data-value') || o.getAttribute('data-value-text') || o.getAttribute('value');
                                                if (txt === val || dv === val) {
                                                    o.click();
                                                    o.dispatchEvent(new Event('click', { bubbles: true }));
                                                    matched = true;
                                                    break;
                                                }
                                            } catch (e) { /* ignore */ }
                                        }
                                    }
                                }

                                if (!matched) {
                                    // fallback: set the input value and try to confirm
                                    sel.value = val;
                                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                                    confirmSelection(sel, val);
                                }
                            } catch (e) {
                                console.log('selection attempt error', e);
                            }

                            if (!matched) {
                                // wait a short time and retry to allow Gradio to render options
                                await new Promise(r => setTimeout(r, 120));
                            }
                        }
                        // small delay to let Gradio update internal state, then fill replacement
                        await new Promise(r => setTimeout(r, 80));
                        try {
                            const repLate = document.querySelector('#gdpr_replacement_input textarea');
                            if (repLate) {
                                repLate.value = val;
                                repLate.dispatchEvent(new Event('input', { bubbles: true }));
                                repLate.dispatchEvent(new Event('change', { bubbles: true }));
                                repLate.focus();
                                if (typeof repLate.select === 'function') { repLate.select(); }
                            }
                        } catch (e) { /* ignore */ }
                    })();
                }
            }

            const rep = document.querySelector('#gdpr_replacement_input textarea');
            if (rep) {
                try {
                    rep.value = val; // pre-fill replacement with the chosen annotation
                    rep.dispatchEvent(new Event('input', { bubbles: true }));
                    rep.dispatchEvent(new Event('change', { bubbles: true }));
                    rep.focus();
                    // Optionally select the text so user can quickly type replacement
                    if (typeof rep.select === 'function') { rep.select(); }
                } catch (e) {
                    rep.focus();
                }
            }

        } catch (e) { console.log('setSelectedEntity error', e); }
    }

    // expose for compatibility
    window.setSelectedEntity = setSelectedEntity;

    // Delegated click handler for links with class 'gdpr-entity-link'
    document.addEventListener('click', function (e) {
        try {
            const t = e.target;
            if (t && t.classList && t.classList.contains('gdpr-entity-link')) {
                e.preventDefault();
                const val = t.getAttribute('data-entity');
                setSelectedEntity(val);
            }
        } catch (err) { console.log('gdpr link click handler error', err); }
    }, true);

})();
