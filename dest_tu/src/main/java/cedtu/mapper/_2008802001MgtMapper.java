package cedtu.mapper;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import org.apache.ibatis.annotations.*;

@Mapper
public interface _2008802001MgtMapper {

    // demo-project/sql/PKG_2008802001_MGT.sql:162 — BIGFUND.PKG_2008802001_MGT.proc_list
    Object selectProcList(@Param("inAccntId") String inAccntId, @Param("inMatchStatus") String inMatchStatus, @Param("inAccntDate1") String inAccntDate1, @Param("inAccntDate2") String inAccntDate2, @Param("inRespondDate1") String inRespondDate1, @Param("inRespondDate2") String inRespondDate2, @Param("inQrybeginpos") String inQrybeginpos, @Param("inQrynum") String inQrynum);
    // demo-project/sql/PKG_2008802001_MGT.sql:162 — BIGFUND.PKG_2008802001_MGT.proc_list
    List<Map<String, Object>> selectProcList_1(@Param("inAccntId") String inAccntId, @Param("inMatchStatus") String inMatchStatus, @Param("inAccntDate1") String inAccntDate1, @Param("inAccntDate2") String inAccntDate2, @Param("inRespondDate1") String inRespondDate1, @Param("inRespondDate2") String inRespondDate2, @Param("inQrybeginpos") String inQrybeginpos, @Param("inQrynum") String inQrynum);
    // demo-project/sql/PKG_2008802001_MGT.sql:357 — BIGFUND.PKG_2008802001_MGT.proc_match
    Map<String, Object> selectProcMatch(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inUserId") String inUserId, @Param("vAmout") java.math.BigDecimal vAmout, @Param("vTradeCode") String vTradeCode);
    // demo-project/sql/PKG_2008802001_MGT.sql:357 — BIGFUND.PKG_2008802001_MGT.proc_match
    int updateProcMatch(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inUserId") String inUserId, @Param("vRespondDate") String vRespondDate);
    // demo-project/sql/PKG_2008802001_MGT.sql:357 — BIGFUND.PKG_2008802001_MGT.proc_match
    String selectProcMatch_1(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inUserId") String inUserId);
    // demo-project/sql/PKG_2008802001_MGT.sql:439 — BIGFUND.PKG_2008802001_MGT.proc_match_account
    java.math.BigDecimal selectProcMatchAccount(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inAmount") Long inAmount, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inUserId") String inUserId);
    // demo-project/sql/PKG_2008802001_MGT.sql:507 — BIGFUND.PKG_2008802001_MGT.proc_modify
    String selectProcModify(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inRespondDate") String inRespondDate, @Param("inUserId") String inUserId);
    // demo-project/sql/PKG_2008802001_MGT.sql:507 — BIGFUND.PKG_2008802001_MGT.proc_modify
    int updateProcModify(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inRespondDate") String inRespondDate, @Param("inUserId") String inUserId);
    // demo-project/sql/PKG_2008802001_MGT.sql:507 — BIGFUND.PKG_2008802001_MGT.proc_modify
    String selectProcModify_1(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inRespondDate") String inRespondDate, @Param("inUserId") String inUserId);
    // demo-project/sql/PKG_2008802001_MGT.sql:584 — BIGFUND.PKG_2008802001_MGT.proc_cancel
    Map<String, Object> selectProcCancel(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inUserId") String inUserId, @Param("vAmout") java.math.BigDecimal vAmout, @Param("vTradeCode") String vTradeCode);
    // demo-project/sql/PKG_2008802001_MGT.sql:584 — BIGFUND.PKG_2008802001_MGT.proc_cancel
    int updateProcCancel(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inAccntSeqno") String inAccntSeqno, @Param("inSeqNo") Long inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq, @Param("inAmount") Long inAmount, @Param("inUserId") String inUserId);
    // demo-project/sql/PKG_2008802001_MGT.sql:650 — BIGFUND.PKG_2008802001_MGT.proc_get_date
    String selectProcGetDate(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") String inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq);
    // demo-project/sql/PKG_2008802001_MGT.sql:699 — BIGFUND.PKG_2008802001_MGT.proc_get_respond_date
    Object selectProcGetRespondDate(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") String inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq);
    // demo-project/sql/PKG_2008802001_MGT.sql:699 — BIGFUND.PKG_2008802001_MGT.proc_get_respond_date
    Object selectProcGetRespondDate_1(@Param("inAccntId") String inAccntId, @Param("inAccntDate") String inAccntDate, @Param("inSeqNo") String inSeqNo, @Param("inInterfaceSeq") Long inInterfaceSeq);
}
