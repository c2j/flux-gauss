package cedtu.service;

import cedtu.exception.BusinessException;
import cedtu.mapper._2008802001MgtMapper;
import java.math.BigDecimal;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.interceptor.TransactionAspectSupport;

@Service
// Source: demo-project/sql/PKG_2008802001_MGT.sql
public class _2008802001MgtService {
    private static final Logger log = LoggerFactory.getLogger(_2008802001MgtService.class);

    private final _2008802001MgtMapper _2008802001MgtMapper;

    public _2008802001MgtService(_2008802001MgtMapper _2008802001MgtMapper) {
            this._2008802001MgtMapper = _2008802001MgtMapper;
    }

        // Source: BIGFUND.PKG_2008802001_MGT.proc_list (PROCEDURE) — demo-project/sql/PKG_2008802001_MGT.sql:162-275
        public void procList(String inAccntId, String inMatchStatus, String inAccntDate1, String inAccntDate2, String inRespondDate1, String inRespondDate2, String inQrybeginpos, String inQrynum, AtomicReference<Long> outFlag, AtomicReference<String> outMsg, AtomicReference<String> totalnum, AtomicReference<Object> outRelCur) {
            String vProcName = null;
            String vStepNo = "";
            List<Map<String, Object>> outRelCurResult = null;
            int outRelCurIdx = 0;
            String __SQLERRM__ = "";
            int __SQLCODE__ = 0;
            try {
            outFlag.set(Long.valueOf("0"));
            outMsg.set("托管服务报告列表");
            vProcName = "pkg_clnt_rpt_info.proc_rpt_list";
            vStepNo = "2";
            vStepNo = "2.1";
            totalnum.set(String.valueOf(_2008802001MgtMapper.selectProcList(inAccntId, inMatchStatus, inAccntDate1, inAccntDate2, inRespondDate1, inRespondDate2, inQrybeginpos, inQrynum)));
            vStepNo = "2.2";
            outRelCurResult = _2008802001MgtMapper.selectProcList_1(inAccntId, inMatchStatus, inAccntDate1, inAccntDate2, inRespondDate1, inRespondDate2, inQrybeginpos, inQrynum);
            if (outRelCurResult == null) outRelCurResult = new java.util.ArrayList<>();
            outRelCurIdx = 0;
            } catch (Exception e) {
            outFlag.set(Long.valueOf("1"));
            outMsg.set(String.valueOf(String.valueOf(String.valueOf(String.valueOf("[ERRCODE:]").concat(String.valueOf(__SQLCODE__))).concat(String.valueOf("ERRMSG"))).concat(String.valueOf(__SQLERRM__))));
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            // CALL pack_log.log(vProcName, outMsg.get(), vStepNo)
            // COMMIT;
            }
        }

        // Source: BIGFUND.PKG_2008802001_MGT.proc_main_ctl (PROCEDURE) — demo-project/sql/PKG_2008802001_MGT.sql:283-349
        public void procMainCtl(String inAccntId, String inAccntDate, String inAccntSeqno, String inAmount, String inSeqNo, String inInterfaceSeq, String inOperFlag, String inRespondDate, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg) {
            List<String> vAccntIdList = this.stringToArray(inAccntId, ",");
            List<String> vAccntSeqnoList = this.stringToArray(inAccntSeqno, ",");
            List<String> vAmountList = this.stringToArray(inAmount, ",");
            List<String> vAccntDateList = this.stringToArray(inAccntDate, ",");
            List<String> vSeqNoList = this.stringToArray(inSeqNo, ",");
            List<String> vInterfaceSeqList = this.stringToArray(inInterfaceSeq, ",");
            for (int i = 1; i <= ((java.util.List<?>) vAccntIdList).size(); i++) {
            if (java.util.Objects.equals(inOperFlag, "1")) {
            this.procMatchAccount(vAccntIdList.get((int)(i) - 1), vAccntDateList.get((int)(i) - 1), vAccntSeqnoList.get((int)(i) - 1), Long.parseLong(String.valueOf(vAmountList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vSeqNoList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vInterfaceSeqList.get((int)(i) - 1))), inUserId, outFlag, outMsg);
            } else if (java.util.Objects.equals(inOperFlag, "2")) {
            this.procModify(vAccntIdList.get((int)(i) - 1), vAccntDateList.get((int)(i) - 1), vAccntSeqnoList.get((int)(i) - 1), Long.parseLong(String.valueOf(vSeqNoList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vInterfaceSeqList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vAmountList.get((int)(i) - 1))), inRespondDate, inUserId, outFlag, outMsg);
            } else if (java.util.Objects.equals(inOperFlag, "3")) {
            this.procCancel(vAccntIdList.get((int)(i) - 1), vAccntDateList.get((int)(i) - 1), vAccntSeqnoList.get((int)(i) - 1), Long.parseLong(String.valueOf(vSeqNoList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vInterfaceSeqList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vAmountList.get((int)(i) - 1))), inUserId, outFlag, outMsg);
            }
            }
        }

        // Source: BIGFUND.PKG_2008802001_MGT.proc_match (PROCEDURE) — demo-project/sql/PKG_2008802001_MGT.sql:357-431
        @Transactional
        public void procMatch(String inAccntId, String inAccntDate, long inSeqNo, long inInterfaceSeq, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg, AtomicReference<String> outDate) {
            AtomicReference<String> vRespondDate = new AtomicReference<>(null);
            String vFlag = null;
            String vTradeCode = null;
            java.math.BigDecimal vAmout = java.math.BigDecimal.ZERO;
            boolean found = false;
            String __SQLERRM__ = "";
            int __SQLCODE__ = 0;
            try {
            outFlag.set(Long.valueOf("0"));
            outMsg.set("匹配成功");
            this.procGetRespondDate(inAccntId, inAccntDate, String.valueOf(inSeqNo), inInterfaceSeq, outFlag, outMsg, vRespondDate);
            try {
            Map<String, Object> _row = _2008802001MgtMapper.selectProcMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, vAmout, vTradeCode);
            if (_row != null) {
                vTradeCode = (_row.get("v_trade_code") instanceof String ? (String) _row.get("v_trade_code") : _row.get("v_trade_code") != null ? String.valueOf(_row.get("v_trade_code")) : null);
                vAmout = (_row.get("v_amout") instanceof java.math.BigDecimal ? (java.math.BigDecimal) _row.get("v_amout") : java.math.BigDecimal.ZERO);
            }
            } catch (Exception e) {
                __SQLERRM__ = e.getMessage();
                __SQLCODE__ = -1;
            }
            _2008802001MgtMapper.updateProcMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, vRespondDate.get());
            // COMMIT;
            try {
            { var _val = _2008802001MgtMapper.selectProcMatch_1(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId); if (_val != null) vFlag = (String) _val; }
            } catch (Exception e) {
                __SQLERRM__ = e.getMessage();
                __SQLCODE__ = -1;
            }
            if (java.util.Objects.equals(vFlag, "2")) {
            outDate.set(String.valueOf(vRespondDate.get()));
            } else {
            outDate.set(String.valueOf(inAccntDate));
            }
            // CALL procSthAccntLog(outDate, inAccntId, vTradeCode, "2008802001", vAmout, inUserId) — procedure not found in current package
            } catch (Exception e) {
            outFlag.set(Long.valueOf("1"));
            outMsg.set(String.valueOf(String.valueOf(String.valueOf(String.valueOf("errcode: ").concat(String.valueOf(__SQLCODE__))).concat(String.valueOf(" errmsg: "))).concat(String.valueOf(__SQLERRM__))));
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            }
        }

        // Source: BIGFUND.PKG_2008802001_MGT.proc_match_account (PROCEDURE) — demo-project/sql/PKG_2008802001_MGT.sql:439-499
        public void procMatchAccount(String inAccntId, String inAccntDate, String inAccntSeqno, long inAmount, long inSeqNo, long inInterfaceSeq, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg) {
            AtomicReference<String> anotherOuterMsg = new AtomicReference<>(null);
            AtomicReference<String> vDate = new AtomicReference<>(null);
            String vInMsg = "0";
            java.math.BigDecimal vInCount = java.math.BigDecimal.ZERO;
            java.math.BigDecimal vCount = java.math.BigDecimal.ZERO;
            AtomicReference<String> outerMsg = new AtomicReference<>(null);
            String __SQLERRM__ = "";
            int __SQLCODE__ = 0;
            try {
            { var _val = _2008802001MgtMapper.selectProcMatchAccount(inAccntId, inAccntDate, inAccntSeqno, inAmount, inSeqNo, inInterfaceSeq, inUserId); if (_val != null) vCount = (java.math.BigDecimal) _val; }
            vInCount = vCount;
            if (java.util.Objects.equals(vCount, 0)) {
            this.procMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, outFlag, anotherOuterMsg, vDate);
            }
            if (vDate.get() != null) {
            while (vDate.get() != null) {
            this.procMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, outFlag, outerMsg, vDate);
            vInMsg = String.valueOf(outerMsg.get());
            }
            // CALL pkg_sth_accnt.proc_sth_set_accnt_info(vDate.get(), inAccntId, "2008802001", inSeqNo, inAmount, "0", inAccntSeqno)
            }
            outFlag.set(Long.valueOf("0"));
            outMsg.set("匹配成功");
            } catch (Exception e) {
            outFlag.set(Long.valueOf("1"));
            outMsg.set(String.valueOf(String.valueOf(String.valueOf(String.valueOf("errcode: ").concat(String.valueOf(__SQLCODE__))).concat(String.valueOf(" errmsg: "))).concat(String.valueOf(__SQLERRM__))));
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            }
        }

        // Source: BIGFUND.PKG_2008802001_MGT.proc_modify (PROCEDURE) — demo-project/sql/PKG_2008802001_MGT.sql:507-575
        @Transactional
        public void procModify(String inAccntId, String inAccntDate, String inAccntSeqno, long inSeqNo, long inInterfaceSeq, long inAmount, String inRespondDate, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg) {
            String vDate = null;
            String vTradeCode = null;
            boolean found = false;
            String __SQLERRM__ = "";
            int __SQLCODE__ = 0;
            try {
            outFlag.set(Long.valueOf("0"));
            outMsg.set("修改成功");
            try {
            { var _val = _2008802001MgtMapper.selectProcModify(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId); if (_val != null) vTradeCode = (String) _val; }
            } catch (Exception e) {
                __SQLERRM__ = e.getMessage();
                __SQLCODE__ = -1;
            }
            _2008802001MgtMapper.updateProcModify(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId);
            // COMMIT;
            try {
            { var _val = _2008802001MgtMapper.selectProcModify_1(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId); if (_val != null) vDate = (String) _val; }
            } catch (Exception e) {
                __SQLERRM__ = e.getMessage();
                __SQLCODE__ = -1;
            vDate = String.valueOf(inAccntDate);
            }
            // CALL procSthAccntLog(vDate, inAccntId, vTradeCode, "2008802001", inAmount, inUserId) — procedure not found in current package
            // CALL pkg_sth_accnt.proc_sth_set_accnt_info(vDate, inAccntId, "2008802001", inSeqNo, inAmount, "0", inAccntSeqno)
            } catch (Exception e) {
            outFlag.set(Long.valueOf("1"));
            outMsg.set(String.valueOf(String.valueOf(String.valueOf(String.valueOf("errcode: ").concat(String.valueOf(__SQLCODE__))).concat(String.valueOf(" errmsg: "))).concat(String.valueOf(__SQLERRM__))));
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            }
        }

        // Source: BIGFUND.PKG_2008802001_MGT.proc_cancel (PROCEDURE) — demo-project/sql/PKG_2008802001_MGT.sql:584-641
        @Transactional
        public void procCancel(String inAccntId, String inAccntDate, String inAccntSeqno, long inSeqNo, long inInterfaceSeq, long inAmount, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg) {
            String vTradeCode = null;
            java.math.BigDecimal vAmout = java.math.BigDecimal.ZERO;
            boolean found = false;
            String __SQLERRM__ = "";
            int __SQLCODE__ = 0;
            try {
            outFlag.set(Long.valueOf("0"));
            outMsg.set("解除匹配成功");
            try {
            Map<String, Object> _row = _2008802001MgtMapper.selectProcCancel(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inUserId, vAmout, vTradeCode);
            if (_row != null) {
                vTradeCode = (_row.get("v_trade_code") instanceof String ? (String) _row.get("v_trade_code") : _row.get("v_trade_code") != null ? String.valueOf(_row.get("v_trade_code")) : null);
                vAmout = (_row.get("v_amout") instanceof java.math.BigDecimal ? (java.math.BigDecimal) _row.get("v_amout") : java.math.BigDecimal.ZERO);
            }
            } catch (Exception e) {
                __SQLERRM__ = e.getMessage();
                __SQLCODE__ = -1;
            }
            _2008802001MgtMapper.updateProcCancel(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inUserId);
            // COMMIT;
            // CALL procSthAccntLog(inAccntDate, inAccntId, vTradeCode, "2008801001", vAmout, inUserId) — procedure not found in current package
            // CALL pkg_sth_accnt.proc_sth_set_accnt_info(inAccntDate, inAccntId, "2008801001", inSeqNo, inAmount, "0", inAccntSeqno)
            } catch (Exception e) {
            outFlag.set(Long.valueOf("1"));
            outMsg.set(String.valueOf(String.valueOf(String.valueOf(String.valueOf("errcode: ").concat(String.valueOf(__SQLCODE__))).concat(String.valueOf(" errmsg: "))).concat(String.valueOf(__SQLERRM__))));
            try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}
            }
        }

        // Source: BIGFUND.PKG_2008802001_MGT.proc_get_date (PROCEDURE) — demo-project/sql/PKG_2008802001_MGT.sql:650-690
        public void procGetDate(String inAccntId, String inAccntDate, String inSeqNo, long inInterfaceSeq, AtomicReference<Long> outFlag, AtomicReference<String> outMsg, AtomicReference<String> outDate) {
            String vFlag = null;
            String __SQLERRM__ = "";
            int __SQLCODE__ = 0;
            try {
            outFlag.set(Long.valueOf("0"));
            outMsg.set("获取入账日期成功");
            try {
            { var _val = _2008802001MgtMapper.selectProcGetDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq); if (_val != null) vFlag = (String) _val; }
            } catch (Exception e) {
                __SQLERRM__ = e.getMessage();
                __SQLCODE__ = -1;
            vFlag = "1";
            }
            if (java.util.Objects.equals(vFlag, "1")) {
            outDate.set(String.valueOf(inAccntDate));
            return;
            } else {
            this.procGetRespondDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, outFlag, outMsg, outDate);
            }
            } catch (Exception e) {
            outFlag.set(Long.valueOf("1"));
            outMsg.set(String.valueOf(String.valueOf(String.valueOf(String.valueOf("errcode: ").concat(String.valueOf(__SQLCODE__))).concat(String.valueOf(" errmsg: "))).concat(String.valueOf(__SQLERRM__))));
            outDate.set(String.valueOf(new java.text.SimpleDateFormat("yyyyMMdd").format(new java.sql.Timestamp(System.currentTimeMillis()))));
            }
        }

        // Source: BIGFUND.PKG_2008802001_MGT.proc_get_respond_date (PROCEDURE) — demo-project/sql/PKG_2008802001_MGT.sql:699-838
        public void procGetRespondDate(String inAccntId, String inAccntDate, String inSeqNo, long inInterfaceSeq, AtomicReference<Long> outFlag, AtomicReference<String> outMsg, AtomicReference<String> outDate) {
            String __SQLERRM__ = "";
            int __SQLCODE__ = 0;
            try {
            outFlag.set(Long.valueOf("0"));
            outMsg.set("获取入账日期成功");
            try {
            outDate.set(String.valueOf(_2008802001MgtMapper.selectProcGetRespondDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq)));
            } catch (Exception e) {
                __SQLERRM__ = e.getMessage();
                __SQLCODE__ = -1;
            outDate.set("");
            }
            if (outDate.get() == null) {
            try {
            outDate.set(String.valueOf(_2008802001MgtMapper.selectProcGetRespondDate_1(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq)));
            } catch (BusinessException e) { // no_data_found
                __SQLERRM__ = e.getMessage();
                __SQLCODE__ = -1;
            outDate.set(String.valueOf(new java.text.SimpleDateFormat("yyyyMMdd").format(new java.sql.Timestamp(System.currentTimeMillis()))));
            }
            }
            if (outDate.get() == null) {
            outDate.set(String.valueOf(new java.text.SimpleDateFormat("yyyyMMdd").format(new java.sql.Timestamp(System.currentTimeMillis()))));
            }
            } catch (Exception e) {
            outFlag.set(Long.valueOf("1"));
            outMsg.set(String.valueOf(String.valueOf(String.valueOf(String.valueOf("errcode: ").concat(String.valueOf(__SQLCODE__))).concat(String.valueOf(" errmsg: "))).concat(String.valueOf(__SQLERRM__))));
            outDate.set(String.valueOf(new java.text.SimpleDateFormat("yyyyMMdd").format(new java.sql.Timestamp(System.currentTimeMillis()))));
            }
        }

    public java.util.List<String> stringToArray(String str, String delimiter) {
        if (str == null || str.isEmpty()) return java.util.Collections.emptyList();
        return java.util.Arrays.asList(str.split(java.util.regex.Pattern.quote(delimiter)));
    }
}
