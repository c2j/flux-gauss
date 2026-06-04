DELETE FROM dat_clr_cash_dtl;
DELETE FROM v_par_asset_acnt_info;
INSERT INTO dat_clr_cash_dtl (account_id, interface_seq, match_status, operation_status) VALUES ('test_account_id', 'test interface_seq', 'test_match_status', 'test_operation_status');
INSERT INTO v_par_asset_acnt_info (accname, asset_acnt_id) VALUES ('test accname', 'test_asset_acnt_id');