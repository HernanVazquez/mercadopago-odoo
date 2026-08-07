/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";

export const FINAL_POINT_STATES = new Set([
    "processed", "failed", "canceled", "expired", "refunded",
]);

export function shouldContinuePolling(snapshot, consecutiveErrors = 0) {
    return Boolean(snapshot && !snapshot.is_final && !FINAL_POINT_STATES.has(snapshot.status)) &&
        consecutiveErrors < 3;
}

export function nextPollDelay(elapsedSeconds) {
    if (elapsedSeconds >= 90) {
        return 10000;
    }
    if (elapsedSeconds >= 30) {
        return 5000;
    }
    return 2500;
}

export class MercadoPagoPointTracking extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            snapshot: null,
            busy: false,
            stopped: false,
            consecutiveErrors: 0,
            scenario: "approved",
            paymentMethodType: "credit_card",
            paymentMethodId: "visa",
            installments: 1,
            rejectionDetail: "insufficient_amount",
        });
        this.timer = null;
        this.mounted = false;
        onWillStart(() => this.loadInitialSnapshot());
        onMounted(() => {
            this.mounted = true;
            this.scheduleNextPoll();
        });
        onWillUnmount(() => {
            this.mounted = false;
            this.stopPolling();
        });
    }

    get wizardId() {
        return this.props.record.resId;
    }

    get methodOptions() {
        const options = this.state.snapshot?.simulation_options || {};
        return this.state.paymentMethodType === "debit_card"
            ? (options.debit_methods || [])
            : (options.credit_methods || []);
    }

    get showCardBrand() {
        return ["credit_card", "debit_card"].includes(this.state.paymentMethodType);
    }

    get elapsedText() {
        const total = this.state.snapshot?.elapsed_seconds || 0;
        const minutes = Math.floor(total / 60);
        const seconds = total % 60;
        return `${minutes}:${String(seconds).padStart(2, "0")}`;
    }

    async loadInitialSnapshot() {
        this.state.busy = true;
        try {
            const snapshot = await this.orm.call(
                "mercadopago.point.tracking.wizard",
                "get_tracking_snapshot",
                [[this.wizardId]]
            );
            this.applySnapshot(snapshot);
        } finally {
            this.state.busy = false;
        }
    }

    applySnapshot(snapshot) {
        this.state.snapshot = snapshot;
        if (snapshot?.poll_error) {
            this.state.consecutiveErrors += 1;
        } else {
            this.state.consecutiveErrors = 0;
        }
        if (!shouldContinuePolling(snapshot, this.state.consecutiveErrors)) {
            this.stopPolling();
            this.state.stopped = !snapshot?.is_final && this.state.consecutiveErrors >= 3;
        }
    }

    scheduleNextPoll() {
        this.clearTimer();
        if (!this.mounted || this.state.busy ||
            !shouldContinuePolling(this.state.snapshot, this.state.consecutiveErrors)) {
            return;
        }
        const delay = nextPollDelay(this.state.snapshot?.elapsed_seconds || 0);
        this.timer = setTimeout(() => this.pollOnce(), delay);
    }

    clearTimer() {
        if (this.timer) {
            clearTimeout(this.timer);
            this.timer = null;
        }
    }

    stopPolling() {
        this.clearTimer();
    }

    async pollOnce(manual = false) {
        if (this.state.busy) {
            return;
        }
        this.clearTimer();
        if (manual) {
            this.state.consecutiveErrors = 0;
            this.state.stopped = false;
        }
        this.state.busy = true;
        try {
            const snapshot = await this.orm.call(
                "mercadopago.point.tracking.wizard",
                "poll_order_status",
                [[this.wizardId]]
            );
            this.applySnapshot(snapshot);
        } catch (error) {
            this.state.consecutiveErrors += 1;
            if (this.state.consecutiveErrors >= 3) {
                this.state.stopped = true;
                this.stopPolling();
            }
        } finally {
            this.state.busy = false;
            this.scheduleNextPoll();
        }
    }

    manualPoll() {
        return this.pollOnce(true);
    }

    onPaymentTypeChange(event) {
        this.state.paymentMethodType = event.target.value;
        if (event.target.value === "credit_card") {
            this.state.paymentMethodId = "visa";
        } else if (event.target.value === "debit_card") {
            this.state.paymentMethodId = "debvisa";
        } else {
            this.state.paymentMethodId = "";
        }
    }

    onInstallmentsInput(event) {
        this.state.installments = event.target.value;
    }

    async simulate() {
        if (this.state.busy || !this.state.snapshot?.can_simulate) {
            return;
        }
        this.clearTimer();
        this.state.busy = true;
        const isCancellation = this.state.scenario === "canceled";
        const isCredit = this.state.paymentMethodType === "credit_card";
        try {
            const snapshot = await this.orm.call(
                "mercadopago.point.tracking.wizard",
                "simulate_test_result",
                [[this.wizardId], this.state.scenario,
                    isCancellation ? false : this.state.paymentMethodType,
                    isCancellation || !this.showCardBrand ? false : this.state.paymentMethodId,
                    isCancellation || !isCredit ? false : Number(this.state.installments),
                    this.state.scenario === "rejected" ? this.state.rejectionDetail : false]
            );
            this.applySnapshot(snapshot);
            if (snapshot.simulation_error) {
                this.notification.add(snapshot.simulation_error, { type: "warning" });
            }
        } catch (error) {
            this.notification.add(error.message || "No se pudo aplicar la simulación TEST.", {
                type: "danger",
            });
        } finally {
            this.state.busy = false;
            this.scheduleNextPoll();
        }
    }
}

MercadoPagoPointTracking.template = "mercadopago_point_odoo.PointTracking";
MercadoPagoPointTracking.props = { ...standardFieldProps };
MercadoPagoPointTracking.supportedTypes = ["char"];

registry.category("fields").add("mercadopago_point_tracking", MercadoPagoPointTracking);
