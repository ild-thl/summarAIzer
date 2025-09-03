function updateQuotesUI() {
    var cb = document.getElementById('quotes_none');
    var none = cb && cb.checked;
    var container = document.getElementById('quotes_questions');
    if (container) {
        container.style.opacity = none ? '0.5' : '1';
        container.style.pointerEvents = none ? 'none' : 'auto';
    }
    var inputs = document.querySelectorAll("input[name='quote_correctness'], input[name='quote_usefulness']");
    inputs.forEach(function (el) { el.required = !none; });
}

document.addEventListener('DOMContentLoaded', function () {
    var cb = document.getElementById('quotes_none');
    if (cb) {
        updateQuotesUI();
        cb.addEventListener('change', updateQuotesUI);
    }
});