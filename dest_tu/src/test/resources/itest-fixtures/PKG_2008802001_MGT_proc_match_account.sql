DELETE FROM dat_clr_cash_dtl;
DELETE FROM dat_zl_batchpayment;
DELETE FROM prm_sth_payback_accnt_date;
DELETE FROM tmp_batchpay_submit;
INSERT INTO dat_clr_cash_dtl (accnt_seqno, account_date, account_id, account_seqno, describe, in_amount, interface_seq, match_status, operation_status, out_amount, respond_date, trade_code) VALUES ('test accnt_seqno', 'test account_date', 'test_account_id', 'test account_seqno', 'test describe', 99.99, 'test interface_seq', 'test_match_status', 'test_operation_status', 99.99, 'test respond_date', 'test_trade_code');
INSERT INTO dat_zl_batchpayment (apaysum, beneaccount, planid) VALUES (99.99, 'test beneaccount', 'test_planid');
INSERT INTO tmp_batchpay_submit (rece_account, status) VALUES ('test rece_account', 'test_status');