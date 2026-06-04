DELETE FROM dat_zl_batchpayment;
DELETE FROM prm_sth_payback_accnt_date;
DELETE FROM tmp_batchpay_submit;
INSERT INTO dat_zl_batchpayment (apaysum, beneaccount, planid) VALUES (99.99, 'test beneaccount', 'test_planid');
INSERT INTO tmp_batchpay_submit (rece_account, status) VALUES ('test rece_account', 'test_status');