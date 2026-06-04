package cedtu.service;

import cedtu.exception.BusinessException;
import cedtu.mapper._2008802001MgtMapper;
import cedtu.service.PackLogService;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: PKG_2008802001_MGT.sql
public class _2008802001MgtService {
    private static final Logger log = LoggerFactory.getLogger(_2008802001MgtService.class);

    private final _2008802001MgtMapper _2008802001MgtMapper;
    private final PackLogService packlogService;

    public _2008802001MgtService(_2008802001MgtMapper _2008802001MgtMapper, PackLogService packlogService) {
        this._2008802001MgtMapper = _2008802001MgtMapper;
        this.packlogService = packlogService;
    }

    public java.util.List<String> stringToArray(String str, String delimiter) {
        if (str == null || str.isEmpty()) return java.util.Collections.emptyList();
        return java.util.Arrays.asList(str.split(java.util.regex.Pattern.quote(delimiter)));
    }
    // Source: PKG_2008802001_MGT.proc_list (PROCEDURE) — PKG_2008802001_MGT.sql:162-275
    // Author  : KFZX-ZHULY
    // Created : 2015/7/1 15:34:15
    // Purpose : 待遇支付退回处理
    // *******************************************************************
    // 存储过程名称：    proc_list
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付退回列表
    // *******************************************************************
    // 受托户id
    // 匹配状态
    // 入款日期
    // 入款日期
    // 反馈日期
    // 反馈日期
    // 查询起始位置
    // 查询记录数量
    // 返成功与否标志，0成功、1失败
    // 返回描述
    // 总记录数
    // 用户查询记录返回集合
    // *******************************************************************
    // 存储过程名称：    proc_main_ctl
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付匹配处理总控程序
    // *******************************************************************
    // 受托户id
    // 日期
    // 核算序列号
    // 入款金额
    // 记账流水号
    // 返回接口流水号(COL_SEQ)
    // 1匹配 2修改 3解除匹配
    // 反馈日期
    // 返成功与否标志，0成功、1失败
    // 返回描述
    // *******************************************************************
    // 存储过程名称：    proc_match
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付匹配
    // *******************************************************************
    // 受托户id
    // 日期
    // 记账流水号
    // 返回接口流水号(COL_SEQ)
    // 返成功与否标志，0成功、1失败
    // 返回描述
    // *******************************************************************
    // 存储过程名称：    proc_match_account
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付匹配核算
    // *******************************************************************
    // 受托户id
    // 日期
    // 核算序列号
    // 入款金额
    // 记账流水号
    // 返回接口流水号(COL_SEQ)
    // 返成功与否标志，0成功、1失败
    // 返回描述
    // *******************************************************************
    // 存储过程名称：    proc_modify
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付信息修改
    // *******************************************************************
    // 受托户id
    // 日期
    // 核算序列号
    // 记账流水号
    // 返回接口流水号(COL_SEQ)
    // 入款金额
    // 反馈日期
    // 返成功与否标志，0成功、1失败
    // 返回描述
    // *******************************************************************
    // 存储过程名称：    proc_cancel
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付解除匹配
    // *******************************************************************
    // 受托户id
    // 日期
    // 核算序列号
    // 记账流水号
    // 返回接口流水号(COL_SEQ)
    // 入款金额
    // 返成功与否标志，0成功、1失败
    // 返回描述
    // *******************************************************************
    // 存储过程名称：    proc_get_date
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    获取入账日期
    // *******************************************************************
    // 受托户id
    // 日期
    // 记账流水号
    // 返回接口流水号(COL_SEQ)
    // 返成功与否标志，0成功、1失败
    // 返回描述
    // 入账日期
    // *******************************************************************
    // 存储过程名称：    proc_get_respond_date
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    获取反馈日期
    // *******************************************************************
    // 受托户id
    // 日期
    // 记账流水号
    // 返回接口流水号(COL_SEQ)
    // 返成功与否标志，0成功、1失败
    // 返回描述
    // 入账日期
    // *******************************************************************
    // 存储过程名称：    proc_list
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付退回列表
    // *******************************************************************
    public void procList(String inAccntId, String inMatchStatus, String inAccntDate1, String inAccntDate2, String inRespondDate1, String inRespondDate2, String inQrybeginpos, String inQrynum, AtomicReference<Long> outFlag, AtomicReference<String> outMsg, AtomicReference<String> totalnum, AtomicReference<List<Map<String, Object>>> outRelCur) {
        String vProcName = null;
        String vStepNo = "";
        int outRelCurIdx = 0;
        List<Map<String, Object>> outRelCurResult = null;
        try {
            outFlag.set(null);
            outMsg.set(null);
            totalnum.set(null);
            outRelCur.set(null);
            // 受托户id
            // 匹配状态
            // 入款日期
            // 入款日期
            // 反馈日期
            // 反馈日期
            // 查询起始位置
            // 查询记录数量
            // 返成功与否标志，0成功、1失败
            // 返回描述
            // 总记录数
            // 用户查询记录返回集合
            // 日志使用变量
            // 存储过程名
            // 步骤名
            outFlag.set(Long.valueOf("0"));
            outMsg.set("托管服务报告列表");
            vProcName = "pkg_clnt_rpt_info.proc_rpt_list";
            vStepNo = "2";
            vStepNo = "2.1";
            // ===========================================
            // 2.1 获取列表总数
            // ===========================================
            // +use_cplan
            totalnum.set(String.valueOf(_2008802001MgtMapper.selectProcList(inAccntId, inMatchStatus, inAccntDate1, inAccntDate2, inRespondDate1, inRespondDate2, inQrybeginpos, inQrynum)));
            // ===========================================
            // 2.2 获取最终结果
            // ===========================================
            vStepNo = "2.2";
            // 记录出错日志
            // 存储过程名
            // 描述
            // 步骤名
            // +use_cplan
            outRelCurResult = _2008802001MgtMapper.selectProcList_1(inAccntId, inMatchStatus, inAccntDate1, inAccntDate2, inRespondDate1, inRespondDate2, inQrybeginpos, inQrynum);
            outRelCurIdx = 0;
            if (outRelCurResult == null) outRelCurResult = new java.util.ArrayList<>();
        } catch (Exception e) { // OTHERS — src: PKG_2008802001_MGT.sql:162
            outFlag.set(Long.valueOf("1"));
            outMsg.set("[ERRCODE:]" + String.valueOf(-1) + "ERRMSG" + e.getMessage());
            // Rollback
            packlogService.log(vProcName, (String) outMsg.get(), vStepNo);
            // Commit
        }
    }

    // Source: PKG_2008802001_MGT.proc_main_ctl (PROCEDURE) — PKG_2008802001_MGT.sql:283-349
    // *******************************************************************
    // 存储过程名称：    proc_main_ctl
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付匹配处理总控程序
    // *******************************************************************
    public void procMainCtl(String inAccntId, String inAccntDate, String inAccntSeqno, String inAmount, String inSeqNo, String inInterfaceSeq, String inOperFlag, String inRespondDate, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg) {
        List<String> vAccntIdList = this.stringToArray(inAccntId, ",");
        List<String> vAccntDateList = this.stringToArray(inAccntDate, ",");
        List<String> vAccntSeqnoList = this.stringToArray(inAccntSeqno, ",");
        List<String> vAmountList = this.stringToArray(inAmount, ",");
        List<String> vSeqNoList = this.stringToArray(inSeqNo, ",");
        List<String> vInterfaceSeqList = this.stringToArray(inInterfaceSeq, ",");
        outFlag.set(null);
        outMsg.set(null);
        // out_flag := '0'; out_msg  := '匹配成功';
        // 受托户id
        // 日期
        // 核算序列号
        // 入款金额
        // 记账流水号
        // 返回接口流水号(COL_SEQ)
        // 1匹配 2修改 3解除匹配
        // 反馈日期
        // 返成功与否标志，0成功、1失败
        // 返回描述
        for (int i = 1; i <= vAccntIdList.size(); i++) {
            if ("1".equals(inOperFlag)) {
                this.procMatchAccount((String) vAccntIdList.get((int)(i) - 1), (String) vAccntDateList.get((int)(i) - 1), (String) vAccntSeqnoList.get((int)(i) - 1), Long.parseLong(String.valueOf(vAmountList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vSeqNoList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vInterfaceSeqList.get((int)(i) - 1))), inUserId, outFlag, outMsg);
            } else if ("2".equals(inOperFlag)) {
                this.procModify((String) vAccntIdList.get((int)(i) - 1), (String) vAccntDateList.get((int)(i) - 1), (String) vAccntSeqnoList.get((int)(i) - 1), Long.parseLong(String.valueOf(vSeqNoList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vInterfaceSeqList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vAmountList.get((int)(i) - 1))), inRespondDate, inUserId, outFlag, outMsg);
            } else if ("3".equals(inOperFlag)) {
                this.procCancel((String) vAccntIdList.get((int)(i) - 1), (String) vAccntDateList.get((int)(i) - 1), (String) vAccntSeqnoList.get((int)(i) - 1), Long.parseLong(String.valueOf(vSeqNoList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vInterfaceSeqList.get((int)(i) - 1))), Long.parseLong(String.valueOf(vAmountList.get((int)(i) - 1))), inUserId, outFlag, outMsg);
            }
        }
    }

    // Source: PKG_2008802001_MGT.proc_match (PROCEDURE) — PKG_2008802001_MGT.sql:357-431
    // *******************************************************************
    // 存储过程名称：    proc_match
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付匹配
    // *******************************************************************
    @Transactional
    public void procMatch(String inAccntId, String inAccntDate, long inSeqNo, long inInterfaceSeq, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg, AtomicReference<String> outDate) {
        String vFlag = null;
        String vTradeCode = null;
        java.math.BigDecimal vAmout = java.math.BigDecimal.ZERO;
        int _sqlRowCount = 0;
        try {
            AtomicReference<String> vRespondDate = new AtomicReference<>(null);
            outFlag.set(null);
            outMsg.set(null);
            outDate.set(null);
            java.util.Map<String, Object> _row = null;
            // 受托户id
            // 日期
            // 记账流水号
            // 返回接口流水号(COL_SEQ)
            // 返成功与否标志，0成功、1失败
            // 返回描述
            outFlag.set(Long.valueOf("0"));
            outMsg.set("匹配成功");
            // ---获取反馈日志逻辑待补充
            this.procGetRespondDate(inAccntId, inAccntDate, String.valueOf(inSeqNo), inInterfaceSeq, outFlag, outMsg, vRespondDate);
            _row = _2008802001MgtMapper.selectProcMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId);
            if (_row == null) _row = java.util.Collections.emptyMap();
            _sqlRowCount = (_row != null && !_row.isEmpty()) ? 1 : 0;
            vTradeCode = (String) _row.get("trade_code");
            vAmout = (_row.get("in_amount") != null ? (_row.get("in_amount") instanceof java.math.BigDecimal ? (java.math.BigDecimal) _row.get("in_amount") : new java.math.BigDecimal(String.valueOf(_row.get("in_amount")))) : java.math.BigDecimal.ZERO);
            _sqlRowCount = _2008802001MgtMapper.updateProcMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, vRespondDate.get());
            // 已匹配
            // 业务类型改为待遇支付退回
            // COMMIT — auto-committed by Spring @Transactional boundary
            vFlag = _2008802001MgtMapper.selectProcMatch_1(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId);
            if ("2".equals(vFlag)) {
                outDate.set(String.valueOf(vRespondDate.get()));
            } else {
                outDate.set(String.valueOf(inAccntDate));
            }
            // END IF;
            // 日志
            // IF v_trade_code <> '2008802001' THEN
            // 入款日期
            // 受托户待遇支付退回入账日期参数没有配，默认去入款日期20151211
            // CALL proc_sth_accnt_log(outDate.get(), inAccntId, vTradeCode, "2008802001", vAmout, inUserId)
        } catch (Exception e) { // OTHERS — src: PKG_2008802001_MGT.sql:357
            outFlag.set(Long.valueOf("1"));
            outMsg.set("errcode: " + String.valueOf(-1) + " errmsg: " + e.getMessage());
            // Rollback
        }
    }

    // Source: PKG_2008802001_MGT.proc_match_account (PROCEDURE) — PKG_2008802001_MGT.sql:439-493
    // *******************************************************************
    // 存储过程名称：    proc_match_account
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付匹配并核算
    // *******************************************************************
    public void procMatchAccount(String inAccntId, String inAccntDate, String inAccntSeqno, long inAmount, long inSeqNo, long inInterfaceSeq, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg) {
        try {
            AtomicReference<String> vDate = new AtomicReference<>(null);
            AtomicReference<String> outerMsg = new AtomicReference<>("2");
            AtomicReference<String> anotherOuterMsg = new AtomicReference<>("2");
            outFlag.set(null);
            outMsg.set(null);
            // out_msg,
            // 受托户id
            // 日期
            // 核算序列号
            // 入款金额
            // 记账流水号
            // 返回接口流水号(COL_SEQ)
            // 返成功与否标志，0成功、1失败
            // 返回描述
            this.procMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, outFlag, anotherOuterMsg, vDate);
            if (vDate.get() != null) {
                while (vDate.get() != null) {
                    this.procMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, outFlag, outerMsg, vDate);
                }
                // CALL pkg_sth_accnt.proc_sth_set_accnt_info(...)
            }
            // 登记核算信息
            // out_msg,
            outFlag.set(Long.valueOf("0"));
            outMsg.set("匹配成功");
        } catch (Exception e) { // OTHERS — src: PKG_2008802001_MGT.sql:439
            outFlag.set(Long.valueOf("1"));
            outMsg.set("errcode: " + String.valueOf(-1) + " errmsg: " + e.getMessage());
            // Rollback
        }
    }

    // Source: PKG_2008802001_MGT.proc_modify (PROCEDURE) — PKG_2008802001_MGT.sql:501-569
    // *******************************************************************
    // 存储过程名称：    proc_modify
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付信息修改
    // *******************************************************************
    @Transactional
    public void procModify(String inAccntId, String inAccntDate, String inAccntSeqno, long inSeqNo, long inInterfaceSeq, long inAmount, String inRespondDate, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg) {
        String vDate = null;
        String vTradeCode = null;
        int _sqlRowCount = 0;
        try {
            outFlag.set(null);
            outMsg.set(null);
            // 受托户id
            // 日期
            // 核算序列号
            // 记账流水号
            // 返回接口流水号(COL_SEQ)
            // 入款金额
            // 反馈日期
            // 返成功与否标志，0成功、1失败
            // 返回描述
            outFlag.set(Long.valueOf("0"));
            outMsg.set("修改成功");
            vTradeCode = _2008802001MgtMapper.selectProcModify(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId);
            _sqlRowCount = _2008802001MgtMapper.updateProcModify(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId);
            // 已匹配
            // 业务类型改为待遇支付退回
            // COMMIT — auto-committed by Spring @Transactional boundary
            vDate = _2008802001MgtMapper.selectProcModify_1(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId);
            // CALL proc_sth_accnt_log(vDate, inAccntId, vTradeCode, "2008802001", inAmount, inUserId)
            // end if;
            // 登记核算信息
            // 受托户待遇支付退回入账日期参数没有配，默认去入款日期20151211
            // 日志
            // if v_trade_code <> '2008802001' then
            // CALL pkg_sth_accnt.proc_sth_set_accnt_info(...)
        } catch (Exception e) { // OTHERS — src: PKG_2008802001_MGT.sql:501
            outFlag.set(Long.valueOf("1"));
            outMsg.set("errcode: " + String.valueOf(-1) + " errmsg: " + e.getMessage());
            // Rollback
        }
    }

    // Source: PKG_2008802001_MGT.proc_cancel (PROCEDURE) — PKG_2008802001_MGT.sql:578-635
    // *******************************************************************
    // 存储过程名称：    proc_cancel
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    受托户待遇支付解除匹配
    // *******************************************************************
    @Transactional
    public void procCancel(String inAccntId, String inAccntDate, String inAccntSeqno, long inSeqNo, long inInterfaceSeq, long inAmount, String inUserId, AtomicReference<Long> outFlag, AtomicReference<String> outMsg) {
        String vTradeCode = null;
        java.math.BigDecimal vAmout = java.math.BigDecimal.ZERO;
        int _sqlRowCount = 0;
        try {
            outFlag.set(null);
            outMsg.set(null);
            java.util.Map<String, Object> _row = null;
            // 受托户id
            // 日期
            // 核算序列号
            // 记账流水号
            // 返回接口流水号(COL_SEQ)
            // 入款金额
            // 返成功与否标志，0成功、1失败
            // 返回描述
            outFlag.set(Long.valueOf("0"));
            outMsg.set("解除匹配成功");
            _row = _2008802001MgtMapper.selectProcCancel(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inUserId);
            if (_row == null) _row = java.util.Collections.emptyMap();
            _sqlRowCount = (_row != null && !_row.isEmpty()) ? 1 : 0;
            vTradeCode = (String) _row.get("trade_code");
            vAmout = (_row.get("in_amount") != null ? (_row.get("in_amount") instanceof java.math.BigDecimal ? (java.math.BigDecimal) _row.get("in_amount") : new java.math.BigDecimal(String.valueOf(_row.get("in_amount")))) : java.math.BigDecimal.ZERO);
            _sqlRowCount = _2008802001MgtMapper.updateProcCancel(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inUserId);
            // 已匹配
            // COMMIT — auto-committed by Spring @Transactional boundary
            // CALL proc_sth_accnt_log(inAccntDate, inAccntId, vTradeCode, "2008801001", vAmout, inUserId)
            // 登记核算信息
            // 日志
            // CALL pkg_sth_accnt.proc_sth_set_accnt_info(...)
        } catch (Exception e) { // OTHERS — src: PKG_2008802001_MGT.sql:578
            outFlag.set(Long.valueOf("1"));
            outMsg.set("errcode: " + String.valueOf(-1) + " errmsg: " + e.getMessage());
            // Rollback
        }
    }

    // Source: PKG_2008802001_MGT.proc_get_date (PROCEDURE) — PKG_2008802001_MGT.sql:644-684
    // *******************************************************************
    // 存储过程名称：    proc_get_date
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    获取入账日期
    // *******************************************************************
    public void procGetDate(String inAccntId, String inAccntDate, String inSeqNo, long inInterfaceSeq, AtomicReference<Long> outFlag, AtomicReference<String> outMsg, AtomicReference<String> outDate) {
        String vFlag = null;
        try {
            outFlag.set(null);
            outMsg.set(null);
            outDate.set(null);
            // 受托户id
            // 日期
            // 记账流水号
            // 返回接口流水号(COL_SEQ)
            // 返成功与否标志，0成功、1失败
            // 返回描述
            // 入账日期
            outFlag.set(Long.valueOf("0"));
            outMsg.set("获取入账日期成功");
            // 受托户待遇支付退回入账日期参数没有配，默认去入款日期20151211
            vFlag = _2008802001MgtMapper.selectProcGetDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq);
            // 入款日期
            if ("1".equals(vFlag)) {
                outDate.set(String.valueOf(inAccntDate));
                return;
            } else {
                this.procGetRespondDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, outFlag, outMsg, outDate);
            }
        } catch (Exception e) { // OTHERS — src: PKG_2008802001_MGT.sql:644
            outFlag.set(Long.valueOf("1"));
            outMsg.set("errcode: " + String.valueOf(-1) + " errmsg: " + e.getMessage());
            outDate.set(String.valueOf(new java.text.SimpleDateFormat("yyyyMMdd").format(new java.sql.Timestamp(System.currentTimeMillis()))));
        }
    }

    // Source: PKG_2008802001_MGT.proc_get_respond_date (PROCEDURE) — PKG_2008802001_MGT.sql:693-832
    // *******************************************************************
    // 存储过程名称：    proc_get_respond_date
    // 作        者：    kfzx-zhuly
    // 时        间：    2015-07-01
    // 存储过程描述：    获取反馈日期
    // *******************************************************************
    public void procGetRespondDate(String inAccntId, String inAccntDate, String inSeqNo, long inInterfaceSeq, AtomicReference<Long> outFlag, AtomicReference<String> outMsg, AtomicReference<String> outDate) {
        try {
            outFlag.set(null);
            outMsg.set(null);
            outDate.set(null);
            // 受托户id
            // 日期
            // 记账流水号
            // 返回接口流水号(COL_SEQ)
            // 返成功与否标志，0成功、1失败
            // 返回描述
            // 入账日期
            // v_accno v_par_asset_acnt_info.accno%TYPE;
            // v_date     dat_batchpay_errback.data_date%TYPE;
            // v_recipacc dat_trustee_acnt_detail.recipacc%TYPE;
            outFlag.set(Long.valueOf("0"));
            outMsg.set("获取入账日期成功");
            outDate.set(String.valueOf(_2008802001MgtMapper.selectProcGetRespondDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq)));
            if (outDate.get() == null) {
                outDate.set(String.valueOf(_2008802001MgtMapper.selectProcGetRespondDate_1(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq)));
            }
            // TXT导入的待遇支付，当有发生待遇支付退回时，批量支付中明细的指令状态为“退款”的明细， 当点击“支付最终完成确认”时，该操作日期为反馈日期
            // SELECT t.accno INTO v_accno FROM v_par_asset_acnt_info t WHERE t.asset_acnt_id = in_accnt_id; SELECT MAX(to_char(t.pay_tm, 'yyyymmdd')) INTO out_date FROM tmp_batchpay_submit t \*, tmp_batch_payment_03092_03093 s*\ WHERE t.status = '26' --退款 AND t.send_account = v_accno AND t.inst_date = in_accnt_date AND t.rece_account = v_recipacc;
            // SELECT MAX(to_char(t.pay_tm, 'yyyymmdd')) INTO out_date FROM tmp_batchpay_submit  t, dat_batchpay_errback b, dat_clr_cash_dtl     s WHERE b.account_seqno = s.account_seqno AND t.referenceno = b.referenceno AND s.account_id = in_accnt_id AND s.account_date = in_accnt_date AND s.account_seqno = in_seq_no AND s.interface_seq = in_interface_seq AND t.planid = b.planid AND t.inst_date = in_accnt_date AND t.status = '26';
            // 反馈日期
            // SELECT b.recipacc INTO v_recipacc FROM dat_clr_cash_dtl a, dat_trustee_acnt_detail b WHERE a.account_id = in_accnt_id AND a.account_date = in_accnt_date AND a.account_seqno = in_seq_no AND a.interface_seq = in_interface_seq AND a.trade_code IN ('2008801001', '2008802001') AND a.interface_seq = b.interface_seq;
            // 直连的反馈日期： 在支付反馈信息中先筛选状态为“失败“的明细与批量支付明细中指令状态为“退款”的明细进行匹配， 能匹配上的明细取支付反馈信息中发送时间字段中的日期为待遇支付退回的反馈日期，即入账日期。
            // 获取划款日期
            // SELECT s.data_date INTO v_date FROM dat_clr_retn_inst t, dat_batchpay_errback s WHERE to_char(t.busitime, 'yyyymmdd') = in_accnt_date AND t.id = s.id AND t.otactno = v_recipacc; \*获取发送日期*\ SELECT MAX(substr(t.send_tm, 0, 8)) INTO out_date FROM dat_zl_batchpayment t, par_sys_plan s WHERE t.data_date = v_date AND t.beneaccount = v_recipacc AND t.planid = s.plan_id AND s.acnt_id = in_accnt_id AND t.successflag = '1'; --失败
            // SELECT MAX(substr(t.send_tm, 0, 8)) INTO out_date FROM dat_zl_batchpayment  t, dat_batchpay_errback b, dat_clr_cash_dtl     s WHERE b.account_seqno = s.account_seqno AND t.referenceno = b.referenceno AND s.account_id = in_accnt_id AND s.account_date = in_accnt_date AND s.account_seqno = in_seq_no AND s.interface_seq = in_interface_seq AND t.planid = b.planid AND t.data_date = b.data_date;
            if (outDate.get() == null) {
                outDate.set(String.valueOf(new java.text.SimpleDateFormat("yyyyMMdd").format(new java.sql.Timestamp(System.currentTimeMillis()))));
            }
        } catch (Exception e) { // OTHERS — src: PKG_2008802001_MGT.sql:693
            outFlag.set(Long.valueOf("1"));
            outMsg.set("errcode: " + String.valueOf(-1) + " errmsg: " + e.getMessage());
            outDate.set(String.valueOf(new java.text.SimpleDateFormat("yyyyMMdd").format(new java.sql.Timestamp(System.currentTimeMillis()))));
        }
    }
}
