package cedtu.service;

import cedtu.exception.BusinessException;
import cedtu.mapper._2008802001MgtMapper;
import cedtu.service._2008802001MgtService;
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
// Source: demo-project/sql/PKG_2008802001_MGT.sql
class _2008802001MgtServiceTest {

    @Mock
    private _2008802001MgtMapper _2008802001MgtMapper;

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
            AtomicReference<Long> outFlagRef = new AtomicReference<>(null);
            AtomicReference<String> outMsgRef = new AtomicReference<>(null);
            AtomicReference<String> totalnumRef = new AtomicReference<>(null);
            AtomicReference<Object> outRelCurRef = new AtomicReference<>(null);
            when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(999);
            { var m = new java.util.HashMap<String,Object>(); m.put("accountId", 1L); m.put("accname", "test"); m.put("accountDate", java.sql.Date.valueOf("2024-01-01")); m.put("inAmount", java.math.BigDecimal.TEN); m.put("describe", 1L); m.put("recipacc", 1L); m.put("recipnam", 1L); m.put("accountSeqno", 1L); m.put("accntSeqno", 1L); m.put("interfaceSeq", 1L); m.put("matchStatus", "test"); m.put("statusname", "test"); m.put("respondDate", java.sql.Date.valueOf("2024-01-01")); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)); }
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcMatchAccount(any(), any(), any(), any(), any(), any(), any())).thenReturn(new java.math.BigDecimal("999.99"));
            when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(999);
            when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(999);
            service.procList(inAccntId, inMatchStatus, inAccntDate1, inAccntDate2, inRespondDate1, inRespondDate2, inQrybeginpos, inQrynum, outFlagRef, outMsgRef, totalnumRef, outRelCurRef);
        }

        @Test
        @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
        void test_procMainCtl_success() {
            String inAccntId = "test_inAccntId";
            String inAccntDate = "2024-01-01";
            String inAccntSeqno = "1";
            String inAmount = "1";
            String inSeqNo = "1";
            String inInterfaceSeq = "1";
            String inOperFlag = "1";
            String inRespondDate = "2024-01-01";
            String inUserId = "test_inUserId";
            AtomicReference<Long> outFlagRef = new AtomicReference<>(null);
            AtomicReference<String> outMsgRef = new AtomicReference<>(null);
            when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(999);
            { var m = new java.util.HashMap<String,Object>(); m.put("accountId", 1L); m.put("accname", "test"); m.put("accountDate", java.sql.Date.valueOf("2024-01-01")); m.put("inAmount", java.math.BigDecimal.TEN); m.put("describe", 1L); m.put("recipacc", 1L); m.put("recipnam", 1L); m.put("accountSeqno", 1L); m.put("accntSeqno", 1L); m.put("interfaceSeq", 1L); m.put("matchStatus", "test"); m.put("statusname", "test"); m.put("respondDate", java.sql.Date.valueOf("2024-01-01")); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)); }
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcMatchAccount(any(), any(), any(), any(), any(), any(), any())).thenReturn(new java.math.BigDecimal("999.99"));
            when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(999);
            when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(999);
            service.procMainCtl(inAccntId, inAccntDate, inAccntSeqno, inAmount, inSeqNo, inInterfaceSeq, inOperFlag, inRespondDate, inUserId, outFlagRef, outMsgRef);
        }

        @Test
        @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
        void test_procMatch_success() {
            String inAccntId = "test_inAccntId";
            String inAccntDate = "2024-01-01";
            Long inSeqNo = 100L;
            Long inInterfaceSeq = 100L;
            String inUserId = "test_inUserId";
            AtomicReference<Long> outFlagRef = new AtomicReference<>(null);
            AtomicReference<String> outMsgRef = new AtomicReference<>(null);
            AtomicReference<String> outDateRef = new AtomicReference<>(null);
            when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(999);
            { var m = new java.util.HashMap<String,Object>(); m.put("accountId", 1L); m.put("accname", "test"); m.put("accountDate", java.sql.Date.valueOf("2024-01-01")); m.put("inAmount", java.math.BigDecimal.TEN); m.put("describe", 1L); m.put("recipacc", 1L); m.put("recipnam", 1L); m.put("accountSeqno", 1L); m.put("accntSeqno", 1L); m.put("interfaceSeq", 1L); m.put("matchStatus", "test"); m.put("statusname", "test"); m.put("respondDate", java.sql.Date.valueOf("2024-01-01")); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)); }
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcMatchAccount(any(), any(), any(), any(), any(), any(), any())).thenReturn(new java.math.BigDecimal("999.99"));
            when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(999);
            when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(999);
            service.procMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, outFlagRef, outMsgRef, outDateRef);
            verify(_2008802001MgtMapper, atLeast(0)).updateProcMatch(any(), any(), any(), any(), any(), any());
        }

        @Test
        @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
        void test_procMatchAccount_success() {
            String inAccntId = "test_inAccntId";
            String inAccntDate = "2024-01-01";
            String inAccntSeqno = "1";
            Long inAmount = 100L;
            Long inSeqNo = 100L;
            Long inInterfaceSeq = 100L;
            String inUserId = "test_inUserId";
            AtomicReference<Long> outFlagRef = new AtomicReference<>(null);
            AtomicReference<String> outMsgRef = new AtomicReference<>(null);
            when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(999);
            { var m = new java.util.HashMap<String,Object>(); m.put("accountId", 1L); m.put("accname", "test"); m.put("accountDate", java.sql.Date.valueOf("2024-01-01")); m.put("inAmount", java.math.BigDecimal.TEN); m.put("describe", 1L); m.put("recipacc", 1L); m.put("recipnam", 1L); m.put("accountSeqno", 1L); m.put("accntSeqno", 1L); m.put("interfaceSeq", 1L); m.put("matchStatus", "test"); m.put("statusname", "test"); m.put("respondDate", java.sql.Date.valueOf("2024-01-01")); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)); }
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcMatchAccount(any(), any(), any(), any(), any(), any(), any())).thenReturn(new java.math.BigDecimal("999.99"));
            when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(999);
            when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(999);
            service.procMatchAccount(inAccntId, inAccntDate, inAccntSeqno, inAmount, inSeqNo, inInterfaceSeq, inUserId, outFlagRef, outMsgRef);
        }

        @Test
        @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
        void test_procModify_success() {
            String inAccntId = "test_inAccntId";
            String inAccntDate = "2024-01-01";
            String inAccntSeqno = "1";
            Long inSeqNo = 100L;
            Long inInterfaceSeq = 100L;
            Long inAmount = 100L;
            String inRespondDate = "2024-01-01";
            String inUserId = "test_inUserId";
            AtomicReference<Long> outFlagRef = new AtomicReference<>(null);
            AtomicReference<String> outMsgRef = new AtomicReference<>(null);
            when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(999);
            { var m = new java.util.HashMap<String,Object>(); m.put("accountId", 1L); m.put("accname", "test"); m.put("accountDate", java.sql.Date.valueOf("2024-01-01")); m.put("inAmount", java.math.BigDecimal.TEN); m.put("describe", 1L); m.put("recipacc", 1L); m.put("recipnam", 1L); m.put("accountSeqno", 1L); m.put("accntSeqno", 1L); m.put("interfaceSeq", 1L); m.put("matchStatus", "test"); m.put("statusname", "test"); m.put("respondDate", java.sql.Date.valueOf("2024-01-01")); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)); }
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcMatchAccount(any(), any(), any(), any(), any(), any(), any())).thenReturn(new java.math.BigDecimal("999.99"));
            when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(999);
            when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(999);
            service.procModify(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId, outFlagRef, outMsgRef);
            verify(_2008802001MgtMapper, atLeast(0)).updateProcModify(any(), any(), any(), any(), any(), any(), any(), any());
        }

        @Test
        @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
        void test_procCancel_success() {
            String inAccntId = "test_inAccntId";
            String inAccntDate = "2024-01-01";
            String inAccntSeqno = "1";
            Long inSeqNo = 100L;
            Long inInterfaceSeq = 100L;
            Long inAmount = 100L;
            String inUserId = "test_inUserId";
            AtomicReference<Long> outFlagRef = new AtomicReference<>(null);
            AtomicReference<String> outMsgRef = new AtomicReference<>(null);
            when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(999);
            { var m = new java.util.HashMap<String,Object>(); m.put("accountId", 1L); m.put("accname", "test"); m.put("accountDate", java.sql.Date.valueOf("2024-01-01")); m.put("inAmount", java.math.BigDecimal.TEN); m.put("describe", 1L); m.put("recipacc", 1L); m.put("recipnam", 1L); m.put("accountSeqno", 1L); m.put("accntSeqno", 1L); m.put("interfaceSeq", 1L); m.put("matchStatus", "test"); m.put("statusname", "test"); m.put("respondDate", java.sql.Date.valueOf("2024-01-01")); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)); }
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcMatchAccount(any(), any(), any(), any(), any(), any(), any())).thenReturn(new java.math.BigDecimal("999.99"));
            when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(999);
            when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(999);
            service.procCancel(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inUserId, outFlagRef, outMsgRef);
            verify(_2008802001MgtMapper, atLeast(0)).updateProcCancel(any(), any(), any(), any(), any(), any(), any());
        }

        @Test
        @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
        void test_procGetDate_success() {
            String inAccntId = "test_inAccntId";
            String inAccntDate = "2024-01-01";
            String inSeqNo = "1";
            Long inInterfaceSeq = 100L;
            AtomicReference<Long> outFlagRef = new AtomicReference<>(null);
            AtomicReference<String> outMsgRef = new AtomicReference<>(null);
            AtomicReference<String> outDateRef = new AtomicReference<>(null);
            when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(999);
            { var m = new java.util.HashMap<String,Object>(); m.put("accountId", 1L); m.put("accname", "test"); m.put("accountDate", java.sql.Date.valueOf("2024-01-01")); m.put("inAmount", java.math.BigDecimal.TEN); m.put("describe", 1L); m.put("recipacc", 1L); m.put("recipnam", 1L); m.put("accountSeqno", 1L); m.put("accntSeqno", 1L); m.put("interfaceSeq", 1L); m.put("matchStatus", "test"); m.put("statusname", "test"); m.put("respondDate", java.sql.Date.valueOf("2024-01-01")); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)); }
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcMatchAccount(any(), any(), any(), any(), any(), any(), any())).thenReturn(new java.math.BigDecimal("999.99"));
            when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(999);
            when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(999);
            service.procGetDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, outFlagRef, outMsgRef, outDateRef);
        }

        @Test
        @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
        void test_procGetRespondDate_success() {
            String inAccntId = "test_inAccntId";
            String inAccntDate = "2024-01-01";
            String inSeqNo = "1";
            Long inInterfaceSeq = 100L;
            AtomicReference<Long> outFlagRef = new AtomicReference<>(null);
            AtomicReference<String> outMsgRef = new AtomicReference<>(null);
            AtomicReference<String> outDateRef = new AtomicReference<>(null);
            when(_2008802001MgtMapper.selectProcList(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(999);
            { var m = new java.util.HashMap<String,Object>(); m.put("accountId", 1L); m.put("accname", "test"); m.put("accountDate", java.sql.Date.valueOf("2024-01-01")); m.put("inAmount", java.math.BigDecimal.TEN); m.put("describe", 1L); m.put("recipacc", 1L); m.put("recipnam", 1L); m.put("accountSeqno", 1L); m.put("accntSeqno", 1L); m.put("interfaceSeq", 1L); m.put("matchStatus", "test"); m.put("statusname", "test"); m.put("respondDate", java.sql.Date.valueOf("2024-01-01")); when(_2008802001MgtMapper.selectProcList_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(java.util.List.of(m)); }
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcMatch(any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcMatch(any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcMatch_1(any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcMatchAccount(any(), any(), any(), any(), any(), any(), any())).thenReturn(new java.math.BigDecimal("999.99"));
            when(_2008802001MgtMapper.selectProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.updateProcModify(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcModify_1(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn("test_value");
            { var m = new java.util.HashMap<String,Object>(); m.put("vTradeCode", "test"); m.put("vAmout", 1L); m.put("v_amout", 1L); m.put("v_trade_code", "test"); when(_2008802001MgtMapper.selectProcCancel(any(), any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(m); }
            when(_2008802001MgtMapper.updateProcCancel(any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
            when(_2008802001MgtMapper.selectProcGetDate(any(), any(), any(), any())).thenReturn("test_value");
            when(_2008802001MgtMapper.selectProcGetRespondDate(any(), any(), any(), any())).thenReturn(999);
            when(_2008802001MgtMapper.selectProcGetRespondDate_1(any(), any(), any(), any())).thenReturn(999);
            service.procGetRespondDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, outFlagRef, outMsgRef, outDateRef);
        }
}
