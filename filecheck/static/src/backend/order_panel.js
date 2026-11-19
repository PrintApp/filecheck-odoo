/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

/**
 * Filecheck tab on the sale order form: per line, the job's status and files
 * (live, short-lived download links and proofs, fetched on open) plus the
 * print files already saved on the order.
 */
export class FilecheckOrderPanel extends Component {
    static template = "filecheck.OrderPanel";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.state = useState({ loading: true, error: null, data: null });
        onWillStart(() => this.load());
    }

    async load() {
        const resId = this.props.record?.resId;
        if (!resId) {
            this.state.loading = false;
            return;
        }
        try {
            this.state.data = await this.orm.call("sale.order", "filecheck_order_details", [[resId]]);
        } catch (e) {
            this.state.error = "Could not load the Filecheck details.";
            console.error(e);
        }
        this.state.loading = false;
    }

    badgeClass(outcome) {
        return {
            pass: "text-bg-success",
            fixed: "text-bg-success",
            warn: "text-bg-warning",
            fail: "text-bg-danger",
        }[outcome] || "text-bg-secondary";
    }
}

registry.category("view_widgets").add("filecheck_order_panel", {
    component: FilecheckOrderPanel,
});
