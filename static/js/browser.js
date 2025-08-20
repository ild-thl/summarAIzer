
function copyReviewLink(url, btn) {
    {
        if (!url) return;
        const abs = (function (u) {
            {
                try { { return new URL(u, window.location.origin).toString(); } } catch (e) { { return u; } }
            }
        })(url);
        const restore = () => setTimeout(() => { { btn.textContent = 'Copy review link'; } }, 1500);
        try {
            {
                if (navigator.clipboard && window.isSecureContext) {
                    {
                        navigator.clipboard.writeText(abs).then(() => {
                            {
                                btn.textContent = 'Copied!';
                                restore();
                            }
                        }).catch(() => { { throw new Error('Clipboard write failed'); } });
                    }
                } else {
                    {
                        throw new Error('Insecure context');
                    }
                }
            }
        } catch (e) {
            {
                // Fallback
                const ta = document.createElement('textarea');
                ta.value = abs;
                ta.style.position = 'fixed';
                ta.style.left = '-9999px';
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                try {
                    {
                        document.execCommand('copy');
                        btn.textContent = 'Copied!';
                    }
                } catch (err) {
                    {
                        btn.textContent = 'Copy failed';
                    }
                } finally {
                    {
                        document.body.removeChild(ta);
                        restore();
                    }
                }
            }
        }
    }
}

document.addEventListener('DOMContentLoaded', function () {
    {
        var el = document.getElementById('review-url-display');
        if (el && el.textContent) {
            {
                try {
                    {
                        el.textContent = new URL(el.textContent, window.location.origin).toString();
                    }
                } catch (e) { { /* ignore */ } }
            }
        }
    }
});