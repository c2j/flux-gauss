        package cedtu.mapper;

        import java.util.List;
import java.util.Map;

        import org.apache.ibatis.annotations.*;

        @Mapper
        public interface _2008802001MgtMapper {

                // PKG_2008802001_MGT.sql:162 — PKG_2008802001_MGT.proc_list
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
        Object selectProcList(@Param("inAccntId") String inAccntId, @Param("inMatchStatus") String inMatchStatus, @Param("inAccntDate1") String inAccntDate1, @Param("inAccntDate2") String inAccntDate2, @Param("inRespondDate1") String inRespondDate1, @Param("inRespondDate2") String inRespondDate2, @Param("inQrybeginpos") String inQrybeginpos, @Param("inQrynum") String inQrynum);
        // PKG_2008802001_MGT.sql:162 — PKG_2008802001_MGT.proc_list
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
        List<Map<String, Object>> selectProcList_1(@Param("inAccntId") String inAccntId, @Param("inMatchStatus") String inMatchStatus, @Param("inAccntDate1") String inAccntDate1, @Param("inAccntDate2") String inAccntDate2, @Param("inRespondDate1") String inRespondDate1, @Param("inRespondDate2") String inRespondDate2, @Param("inQrybeginpos") String inQrybeginpos, @Param("inQrynum") String inQrynum);
        // PKG_2008802001_MGT.sql:357 — PKG_2008802001_MGT.proc_match
        // *******************************************************************
        // 存储过程名称：    proc_match
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    受托户待遇支付匹配
        // *******************************************************************
        Map<String, Object> selectProcMatch(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inUserId") String inUserId);
        // PKG_2008802001_MGT.sql:357 — PKG_2008802001_MGT.proc_match
        // *******************************************************************
        // 存储过程名称：    proc_match
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    受托户待遇支付匹配
        // *******************************************************************
        int updateProcMatch(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inUserId") String inUserId, @Param("vRespondDate") String vRespondDate);
        // PKG_2008802001_MGT.sql:357 — PKG_2008802001_MGT.proc_match
        // *******************************************************************
        // 存储过程名称：    proc_match
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    受托户待遇支付匹配
        // *******************************************************************
        String selectProcMatch_1(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inUserId") String inUserId);
        // PKG_2008802001_MGT.sql:501 — PKG_2008802001_MGT.proc_modify
        // *******************************************************************
        // 存储过程名称：    proc_modify
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    受托户待遇支付信息修改
        // *******************************************************************
        String selectProcModify(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inRespondDate") String inRespondDate, @Param("inUserId") String inUserId);
        // PKG_2008802001_MGT.sql:501 — PKG_2008802001_MGT.proc_modify
        // *******************************************************************
        // 存储过程名称：    proc_modify
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    受托户待遇支付信息修改
        // *******************************************************************
        int updateProcModify(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inRespondDate") String inRespondDate, @Param("inUserId") String inUserId);
        // PKG_2008802001_MGT.sql:501 — PKG_2008802001_MGT.proc_modify
        // *******************************************************************
        // 存储过程名称：    proc_modify
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    受托户待遇支付信息修改
        // *******************************************************************
        String selectProcModify_1(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inRespondDate") String inRespondDate, @Param("inUserId") String inUserId);
        // PKG_2008802001_MGT.sql:578 — PKG_2008802001_MGT.proc_cancel
        // *******************************************************************
        // 存储过程名称：    proc_cancel
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    受托户待遇支付解除匹配
        // *******************************************************************
        Map<String, Object> selectProcCancel(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inUserId") String inUserId);
        // PKG_2008802001_MGT.sql:578 — PKG_2008802001_MGT.proc_cancel
        // *******************************************************************
        // 存储过程名称：    proc_cancel
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    受托户待遇支付解除匹配
        // *******************************************************************
        int updateProcCancel(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inUserId") String inUserId);
        // PKG_2008802001_MGT.sql:644 — PKG_2008802001_MGT.proc_get_date
        // *******************************************************************
        // 存储过程名称：    proc_get_date
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    获取入账日期
        // *******************************************************************
        String selectProcGetDate(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") String inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq);
        // PKG_2008802001_MGT.sql:693 — PKG_2008802001_MGT.proc_get_respond_date
        // *******************************************************************
        // 存储过程名称：    proc_get_respond_date
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    获取反馈日期
        // *******************************************************************
        Object selectProcGetRespondDate(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") String inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq);
        // PKG_2008802001_MGT.sql:693 — PKG_2008802001_MGT.proc_get_respond_date
        // *******************************************************************
        // 存储过程名称：    proc_get_respond_date
        // 作        者：    kfzx-zhuly
        // 时        间：    2015-07-01
        // 存储过程描述：    获取反馈日期
        // *******************************************************************
        Object selectProcGetRespondDate_1(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") String inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq);
        }
