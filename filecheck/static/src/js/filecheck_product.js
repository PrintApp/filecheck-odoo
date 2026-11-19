/*
 * Filecheck on the Odoo product page.
 *
 * Filecheck.mount() (from the per-key CDN bundle) mounts the upload widget,
 * gates the purchase buttons on canProceed and handles the proof gallery.
 * This script adds the Odoo-specific parts:
 *  - Odoo's Add to Cart / Buy Now are <a> elements: mount() can only mark them
 *    aria-disabled + .filecheck-gated, so clicks on gated buttons are stopped
 *    here (capture phase, before website_sale's delegated handlers).
 *  - add-to-cart is a JSON call that posts none of the page's inputs, so every
 *    status change is saved to the Odoo session (/filecheck/save_job), where
 *    the cart picks the job up. Buttons stay gated until that save lands.
 */
(function () {
    'use strict';

    var paramsEl = document.getElementById('filecheck-params');
    if (!paramsEl) return;
    var p;
    try { p = JSON.parse(paramsEl.textContent || '{}'); } catch (e) { return; }
    if (!p.publishableKey || !p.workflowId) return;

    var SAVING = 'o_filecheck_saving';
    var GATED = '.filecheck-gated, .' + SAVING;

    document.addEventListener('click', function (ev) {
        var target = ev.target && ev.target.closest && ev.target.closest(GATED);
        if (target) {
            ev.preventDefault();
            ev.stopImmediatePropagation();
        }
    }, true);

    function cartButtons() {
        return Array.prototype.slice.call(document.querySelectorAll(p.cartButtonSelector || '#add_to_cart'));
    }

    function setSaving(on) {
        cartButtons().forEach(function (btn) { btn.classList.toggle(SAVING, on); });
    }

    var lastKey = null;
    var inflight = 0;

    function save(status) {
        var jobId = status.jobId || null;
        var ready = !!(jobId && status.canProceed);
        var files = (status.files || []).map(function (f) { return f && f.name; }).filter(Boolean);
        var key = [jobId, ready, files.join('|')].join('#');
        if (key === lastKey) return;
        lastKey = key;

        if (ready) setSaving(true);
        inflight += 1;
        fetch(p.endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jsonrpc: '2.0',
                method: 'call',
                params: { product_id: p.productId, job_id: jobId, ready: ready, files: files },
            }),
        }).catch(function (err) {
            lastKey = null; // let the next status event retry
            console.warn('[Filecheck] could not save the job to the session', err);
        }).then(function () {
            inflight -= 1;
            if (!inflight) setSaving(false);
        });
    }

    function whenReady(cb) {
        var started = Date.now();
        (function tick() {
            if (window.Filecheck && window.Filecheck.mount) return cb();
            if (Date.now() - started > 15000) {
                console.error('[Filecheck] the Filecheck element did not load');
                return;
            }
            setTimeout(tick, 50);
        })();
    }

    function init() {
        whenReady(function () {
            var config = Object.assign({}, window.FILECHECK_CONFIG || {}, {
                publishableKey: p.publishableKey,
                workflowId: p.workflowId,
                presentation: p.presentation || 'inline',
                agentId: p.agentId || null,
                mountSelector: '#fc-slot-' + p.productId,
                cartButtonSelector: p.cartButtonSelector,
                // Always set, 'checkout' included: the product's setting
                // decides both this page and which order lines Odoo sends as
                // waiting for artwork, so the account default must not win.
                artworkTiming: p.artworkTiming || 'checkout',
            });
            if (p.connectorId) config.connectorId = p.connectorId;
            if (p.jobId) config.jobId = p.jobId;
            if (p.locale) config.locale = p.locale;

            var el = window.Filecheck.mount(config);
            if (el) el.on('status', save);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
