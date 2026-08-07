/** @odoo-module **/

import {
    nextPollDelay,
    shouldContinuePolling,
} from "@mercadopago_point_odoo/js/point_tracking";

QUnit.module("mercadopago_point_odoo", () => {
    QUnit.module("Point tracking polling");

    QUnit.test("polling stops for every final state", (assert) => {
        for (const status of ["processed", "failed", "canceled", "expired", "refunded"]) {
            assert.notOk(
                shouldContinuePolling({ status, is_final: true }, 0),
                `polling stops for ${status}`
            );
        }
    });

    QUnit.test("polling backs off and stops after repeated errors", (assert) => {
        const active = { status: "created", is_final: false };
        assert.strictEqual(nextPollDelay(0), 2500);
        assert.strictEqual(nextPollDelay(30), 5000);
        assert.strictEqual(nextPollDelay(90), 10000);
        assert.ok(shouldContinuePolling(active, 2));
        assert.notOk(shouldContinuePolling(active, 3));
    });
});
