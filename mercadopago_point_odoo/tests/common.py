"""Shared, isolated accounting fixtures for Mercado Pago Point tests."""

from odoo.tests.common import TransactionCase


class MercadoPagoPointCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.currency_ars = cls.env.ref("base.ARS")
        cls.currency_ars.active = True
        cls.company = cls.env["res.company"].create({
            "name": "Mercado Pago Point Test Company",
            "currency_id": cls.currency_ars.id,
        })
        cls.env.user.write({"company_ids": [(4, cls.company.id)]})

        account_model = cls.env["account.account"].with_company(cls.company)
        cls.receivable_account = account_model.create({
            "name": "Point Test Receivable",
            "code": "MPTRCV",
            "account_type": "asset_receivable",
            "reconcile": True,
            "company_id": cls.company.id,
        })
        cls.payable_account = account_model.create({
            "name": "Point Test Payable",
            "code": "MPTPAY",
            "account_type": "liability_payable",
            "reconcile": True,
            "company_id": cls.company.id,
        })
        cls.liquidity_account = account_model.create({
            "name": "Point Test Bank",
            "code": "MPTBNK",
            "account_type": "asset_cash",
            "company_id": cls.company.id,
        })
        cls.outstanding_receipts_account = account_model.create({
            "name": "Point Test Outstanding Receipts",
            "code": "MPTOUT",
            "account_type": "asset_current",
            "reconcile": True,
            "company_id": cls.company.id,
        })
        cls.journal = cls.env["account.journal"].with_company(cls.company).create({
            "name": "Point Test Bank Journal",
            "code": "MPTB",
            "type": "bank",
            "company_id": cls.company.id,
            "default_account_id": cls.liquidity_account.id,
        })
        cls.inbound_payment_method_line = (
            cls.journal.inbound_payment_method_line_ids.filtered(
                lambda line: line.payment_method_id.code == "manual"
            )[:1]
        )
        if not cls.inbound_payment_method_line:
            manual_method = cls.env.ref("account.account_payment_method_manual_in")
            cls.inbound_payment_method_line = cls.env[
                "account.payment.method.line"
            ].with_company(cls.company).create({
                "name": "Manual",
                "payment_method_id": manual_method.id,
                "journal_id": cls.journal.id,
            })
        cls.inbound_payment_method_line.payment_account_id = (
            cls.outstanding_receipts_account
        )
        cls.partner_a = cls.env["res.partner"].with_company(cls.company).create({
            "name": "Point Test Customer",
            "company_id": False,
            "property_account_receivable_id": cls.receivable_account.id,
            "property_account_payable_id": cls.payable_account.id,
        })

        cls.point_method = cls.env.ref(
            "mercadopago_point_odoo.account_payment_method_mercadopago_point"
        )
        cls.point_lines_created_automatically = cls.env[
            "account.payment.method.line"
        ].search_count([
            ("payment_method_id", "=", cls.point_method.id),
            ("company_id", "=", cls.company.id),
        ])
        cls.point_config = cls.env["mercadopago.point.config"].with_company(
            cls.company
        ).create({
            "name": "Virtual Point TEST",
            "company_id": cls.company.id,
            "environment": "test",
            "access_token": "TEST-access-token-never-log",
            "terminal_id": "VIRTUAL_POINT_TEST_001",
            "timeout_seconds": 10,
        })
        cls.point_method_line = cls.env[
            "account.payment.method.line"
        ].with_company(cls.company).create({
            "name": "Mercado Pago Point",
            "payment_method_id": cls.point_method.id,
            "journal_id": cls.journal.id,
            "payment_account_id": cls.outstanding_receipts_account.id,
            "mercadopago_point_config_id": cls.point_config.id,
        })

        cls.qr_method = cls.env.ref(
            "mercadopago_point_odoo.account_payment_method_mercadopago_qr"
        )
        cls.qr_lines_created_automatically = cls.env[
            "account.payment.method.line"
        ].search_count([
            ("payment_method_id", "=", cls.qr_method.id),
            ("company_id", "=", cls.company.id),
        ])
        cls.qr_config = cls.env["mercadopago.point.config"].with_company(
            cls.company
        ).create({
            "name": "QR Cash Register TEST",
            "company_id": cls.company.id,
            "environment": "test",
            "integration_type": "qr",
            "access_token": "TEST-qr-access-token-never-log",
            "external_pos_id": "QR_POS_TEST_001",
            "qr_mode": "hybrid",
            "timeout_seconds": 10,
        })
        cls.qr_method_line = cls.env[
            "account.payment.method.line"
        ].with_company(cls.company).create({
            "name": "Mercado Pago QR",
            "payment_method_id": cls.qr_method.id,
            "journal_id": cls.journal.id,
            "payment_account_id": cls.outstanding_receipts_account.id,
            "mercadopago_point_config_id": cls.qr_config.id,
        })

    def _create_point_payment(self, amount=100.0):
        return self.env["account.payment"].with_company(self.company).create({
            "company_id": self.company.id,
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": self.partner_a.id,
            "destination_account_id": self.receivable_account.id,
            "amount": amount,
            "currency_id": self.currency_ars.id,
            "journal_id": self.journal.id,
            "payment_method_line_id": self.point_method_line.id,
        })

    def _create_qr_payment(self, amount=100.0):
        return self.env["account.payment"].with_company(self.company).create({
            "company_id": self.company.id,
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": self.partner_a.id,
            "destination_account_id": self.receivable_account.id,
            "amount": amount,
            "currency_id": self.currency_ars.id,
            "journal_id": self.journal.id,
            "payment_method_line_id": self.qr_method_line.id,
        })
