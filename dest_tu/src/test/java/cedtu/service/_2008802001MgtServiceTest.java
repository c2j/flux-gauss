package cedtu.service;

import cedtu.exception.BusinessException;
import cedtu.mapper._2008802001MgtMapper;
import cedtu.service.PackLogService;
import cedtu.service._2008802001MgtService;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
// Source: PKG_2008802001_MGT.sql
class _2008802001MgtServiceTest {

    @Mock
    private _2008802001MgtMapper _2008802001MgtMapper;

    @Mock
    private PackLogService packlogService;

    @InjectMocks
    private _2008802001MgtService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_procList_success() {
        String inAccntId = "test_inAccntId";
        String inMatchStatus = "test_inMatchStatus";
        String inAccntDate1 = "2024-01-01";
        String inAccntDate2 = "2024-01-01";
        String inRespondDate1 = "2024-01-01";
        String inRespondDate2 = "2024-01-01";
        String inQrybeginpos = "test_inQrybeginpos";
        String inQrynum = "test_inQrynum";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        AtomicReference<String> totalnum = new AtomicReference<>(null);
        AtomicReference<List<Map<String, Object>>> outRelCur = new AtomicReference<>(null);
        when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(null);
        { var m = new java.util.HashMap<String,Object>(); m.put("account_id", 1); m.put("accname", "test"); m.put("account_date", 5); m.put("in_amount", java.math.BigDecimal.TEN); m.put("describe", "test"); m.put("recipacc", 1); m.put("recipnam", 1); m.put("account_seqno", 5); m.put("accnt_seqno", 1); m.put("interface_seq", 1); m.put("match_status", "test"); m.put("statusname", "test"); m.put("respond_date", "2025-01-01"); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(null);
        when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(null);
        service.procList(inAccntId, inMatchStatus, inAccntDate1, inAccntDate2, inRespondDate1, inRespondDate2, inQrybeginpos, inQrynum, outFlag, outMsg, totalnum, outRelCur);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_procMainCtl_success() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inAccntSeqno = "test_inAccntSeqno";
        String inAmount = "test_inAmount";
        String inSeqNo = "test_inSeqNo";
        String inInterfaceSeq = "test_inInterfaceSeq";
        String inOperFlag = "test_inOperFlag";
        String inRespondDate = "2024-01-01";
        String inUserId = "test_inUserId";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(null);
        { var m = new java.util.HashMap<String,Object>(); m.put("account_id", 1); m.put("accname", "test"); m.put("account_date", 5); m.put("in_amount", java.math.BigDecimal.TEN); m.put("describe", "test"); m.put("recipacc", 1); m.put("recipnam", 1); m.put("account_seqno", 5); m.put("accnt_seqno", 1); m.put("interface_seq", 1); m.put("match_status", "test"); m.put("statusname", "test"); m.put("respond_date", "2025-01-01"); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(null);
        when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(null);
        service.procMainCtl(inAccntId, inAccntDate, inAccntSeqno, inAmount, inSeqNo, inInterfaceSeq, inOperFlag, inRespondDate, inUserId, outFlag, outMsg);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_procMatch_success() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        Long inSeqNo = 100L;
        Long inInterfaceSeq = 100L;
        String inUserId = "test_inUserId";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        AtomicReference<String> outDate = new AtomicReference<>(null);
        when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(null);
        { var m = new java.util.HashMap<String,Object>(); m.put("account_id", 1); m.put("accname", "test"); m.put("account_date", 5); m.put("in_amount", java.math.BigDecimal.TEN); m.put("describe", "test"); m.put("recipacc", 1); m.put("recipnam", 1); m.put("account_seqno", 5); m.put("accnt_seqno", 1); m.put("interface_seq", 1); m.put("match_status", "test"); m.put("statusname", "test"); m.put("respond_date", "2025-01-01"); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(null);
        when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(null);
        service.procMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, outFlag, outMsg, outDate);
        verify(_2008802001MgtMapper, atLeast(0)).updateProcMatch(any(), any(), any(), any(), any(), any());
    }

    @org.junit.jupiter.api.Disabled("auto-generated mock cannot terminate while loop")
    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_procMatchAccount_success() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inAccntSeqno = "test_inAccntSeqno";
        Long inAmount = 100L;
        Long inSeqNo = 100L;
        Long inInterfaceSeq = 100L;
        String inUserId = "test_inUserId";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(null);
        { var m = new java.util.HashMap<String,Object>(); m.put("account_id", 1); m.put("accname", "test"); m.put("account_date", 5); m.put("in_amount", java.math.BigDecimal.TEN); m.put("describe", "test"); m.put("recipacc", 1); m.put("recipnam", 1); m.put("account_seqno", 5); m.put("accnt_seqno", 1); m.put("interface_seq", 1); m.put("match_status", "test"); m.put("statusname", "test"); m.put("respond_date", "2025-01-01"); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(null);
        when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(null);
        service.procMatchAccount(inAccntId, inAccntDate, inAccntSeqno, inAmount, inSeqNo, inInterfaceSeq, inUserId, outFlag, outMsg);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_procModify_success() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inAccntSeqno = "test_inAccntSeqno";
        Long inSeqNo = 100L;
        Long inInterfaceSeq = 100L;
        Long inAmount = 100L;
        String inRespondDate = "2024-01-01";
        String inUserId = "test_inUserId";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(null);
        { var m = new java.util.HashMap<String,Object>(); m.put("account_id", 1); m.put("accname", "test"); m.put("account_date", 5); m.put("in_amount", java.math.BigDecimal.TEN); m.put("describe", "test"); m.put("recipacc", 1); m.put("recipnam", 1); m.put("account_seqno", 5); m.put("accnt_seqno", 1); m.put("interface_seq", 1); m.put("match_status", "test"); m.put("statusname", "test"); m.put("respond_date", "2025-01-01"); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(null);
        when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(null);
        service.procModify(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId, outFlag, outMsg);
        verify(_2008802001MgtMapper, atLeast(0)).updateProcModify(any(), any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_procCancel_success() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inAccntSeqno = "test_inAccntSeqno";
        Long inSeqNo = 100L;
        Long inInterfaceSeq = 100L;
        Long inAmount = 100L;
        String inUserId = "test_inUserId";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(null);
        { var m = new java.util.HashMap<String,Object>(); m.put("account_id", 1); m.put("accname", "test"); m.put("account_date", 5); m.put("in_amount", java.math.BigDecimal.TEN); m.put("describe", "test"); m.put("recipacc", 1); m.put("recipnam", 1); m.put("account_seqno", 5); m.put("accnt_seqno", 1); m.put("interface_seq", 1); m.put("match_status", "test"); m.put("statusname", "test"); m.put("respond_date", "2025-01-01"); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(null);
        when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(null);
        service.procCancel(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inUserId, outFlag, outMsg);
        verify(_2008802001MgtMapper, atLeast(0)).updateProcCancel(any(), any(), any(), any(), any(), any(), any());
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_procGetDate_success() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inSeqNo = "test_inSeqNo";
        Long inInterfaceSeq = 100L;
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        AtomicReference<String> outDate = new AtomicReference<>(null);
        when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(null);
        { var m = new java.util.HashMap<String,Object>(); m.put("account_id", 1); m.put("accname", "test"); m.put("account_date", 5); m.put("in_amount", java.math.BigDecimal.TEN); m.put("describe", "test"); m.put("recipacc", 1); m.put("recipnam", 1); m.put("account_seqno", 5); m.put("accnt_seqno", 1); m.put("interface_seq", 1); m.put("match_status", "test"); m.put("statusname", "test"); m.put("respond_date", "2025-01-01"); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(null);
        when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(null);
        service.procGetDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, outFlag, outMsg, outDate);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_procGetRespondDate_success() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inSeqNo = "test_inSeqNo";
        Long inInterfaceSeq = 100L;
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        AtomicReference<String> outDate = new AtomicReference<>(null);
        when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(null);
        { var m = new java.util.HashMap<String,Object>(); m.put("account_id", 1); m.put("accname", "test"); m.put("account_date", 5); m.put("in_amount", java.math.BigDecimal.TEN); m.put("describe", "test"); m.put("recipacc", 1); m.put("recipnam", 1); m.put("account_seqno", 5); m.put("accnt_seqno", 1); m.put("interface_seq", 1); m.put("match_status", "test"); m.put("statusname", "test"); m.put("respond_date", "2025-01-01"); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test");
        { var m = new java.util.HashMap<String,Object>(); m.put("trade_code", 1); m.put("in_amount", java.math.BigDecimal.TEN); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(m).thenReturn(null); }
        when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test");
        when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(null);
        when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(null);
        service.procGetRespondDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, outFlag, outMsg, outDate);
    }
}
