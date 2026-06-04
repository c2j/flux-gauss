package cedtu.itest;

import cedtu.itest.AbstractIntegrationTest;
import cedtu.mapper._2008802001MgtMapper;
import cedtu.service.PackLogService;
import cedtu.service._2008802001MgtService;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.springframework.beans.factory.annotation.Autowired;
import static org.junit.jupiter.api.Assertions.*;

// Source: PKG_2008802001_MGT.sql
class _2008802001MgtServiceIntegrationTest extends AbstractIntegrationTest {

    @Autowired
    private _2008802001MgtMapper _2008802001MgtMapper;

    @Autowired
    private _2008802001MgtService _2008802001MgtService;

    @Autowired
    private PackLogService packlogService;

    @org.springframework.test.context.jdbc.Sql(scripts = "classpath:itest-fixtures/PKG_2008802001_MGT_proc_list.sql", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))
    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    void test_procList_integration() {
        String inAccntId = "test_inAccntId";
        String inMatchStatus = "test_inMatchStatus";
        String inAccntDate1 = "2024-01-01";
        String inAccntDate2 = "2024-01-01";
        String inRespondDate1 = "2024-01-01";
        String inRespondDate2 = "2024-01-01";
        String inQrybeginpos = "1";
        String inQrynum = "1";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        AtomicReference<String> totalnum = new AtomicReference<>(null);
        AtomicReference<List<Map<String, Object>>> outRelCur = new AtomicReference<>(null);
        _2008802001MgtService.procList(inAccntId, inMatchStatus, inAccntDate1, inAccntDate2, inRespondDate1, inRespondDate2, inQrybeginpos, inQrynum, outFlag, outMsg, totalnum, outRelCur);
        // TODO: Add assertions for OUT parameters (values depend on test data)
        // assertNotNull(outFlag.get());
        // assertNotNull(outMsg.get());
        // assertNotNull(totalnum.get());
        // assertNotNull(outRelCur.get());
    }

    @org.springframework.test.context.jdbc.Sql(scripts = "classpath:itest-fixtures/PKG_2008802001_MGT_proc_main_ctl.sql", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))
    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    void test_procMainCtl_integration() {
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
        _2008802001MgtService.procMainCtl(inAccntId, inAccntDate, inAccntSeqno, inAmount, inSeqNo, inInterfaceSeq, inOperFlag, inRespondDate, inUserId, outFlag, outMsg);
        // TODO: Add assertions for OUT parameters (values depend on test data)
        // assertNotNull(outFlag.get());
        // assertNotNull(outMsg.get());
    }

    @org.springframework.test.context.jdbc.Sql(scripts = "classpath:itest-fixtures/PKG_2008802001_MGT_proc_match.sql", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))
    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    void test_procMatch_integration() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        Long inSeqNo = 100L;
        Long inInterfaceSeq = 100L;
        String inUserId = "test_inUserId";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        AtomicReference<String> outDate = new AtomicReference<>(null);
        _2008802001MgtService.procMatch(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, inUserId, outFlag, outMsg, outDate);
        // TODO: Add assertions for OUT parameters (values depend on test data)
        // assertNotNull(outFlag.get());
        // assertNotNull(outMsg.get());
        // assertNotNull(outDate.get());
    }

    @Disabled("auto-generated itest cannot terminate while loop")
    @org.springframework.test.context.jdbc.Sql(scripts = "classpath:itest-fixtures/PKG_2008802001_MGT_proc_match_account.sql", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))
    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    void test_procMatchAccount_integration() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inAccntSeqno = "test_inAccntSeqno";
        Long inAmount = 100L;
        Long inSeqNo = 100L;
        Long inInterfaceSeq = 100L;
        String inUserId = "test_inUserId";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        _2008802001MgtService.procMatchAccount(inAccntId, inAccntDate, inAccntSeqno, inAmount, inSeqNo, inInterfaceSeq, inUserId, outFlag, outMsg);
        // TODO: Add assertions for OUT parameters (values depend on test data)
        // assertNotNull(outFlag.get());
        // assertNotNull(outMsg.get());
    }

    @org.springframework.test.context.jdbc.Sql(scripts = "classpath:itest-fixtures/PKG_2008802001_MGT_proc_modify.sql", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))
    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    void test_procModify_integration() {
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
        _2008802001MgtService.procModify(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inRespondDate, inUserId, outFlag, outMsg);
        // TODO: Add assertions for OUT parameters (values depend on test data)
        // assertNotNull(outFlag.get());
        // assertNotNull(outMsg.get());
    }

    @org.springframework.test.context.jdbc.Sql(scripts = "classpath:itest-fixtures/PKG_2008802001_MGT_proc_cancel.sql", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))
    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    void test_procCancel_integration() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inAccntSeqno = "test_inAccntSeqno";
        Long inSeqNo = 100L;
        Long inInterfaceSeq = 100L;
        Long inAmount = 100L;
        String inUserId = "test_inUserId";
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        _2008802001MgtService.procCancel(inAccntId, inAccntDate, inAccntSeqno, inSeqNo, inInterfaceSeq, inAmount, inUserId, outFlag, outMsg);
        // TODO: Add assertions for OUT parameters (values depend on test data)
        // assertNotNull(outFlag.get());
        // assertNotNull(outMsg.get());
    }

    @org.springframework.test.context.jdbc.Sql(scripts = "classpath:itest-fixtures/PKG_2008802001_MGT_proc_get_date.sql", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))
    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    void test_procGetDate_integration() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inSeqNo = "test_inSeqNo";
        Long inInterfaceSeq = 100L;
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        AtomicReference<String> outDate = new AtomicReference<>(null);
        _2008802001MgtService.procGetDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, outFlag, outMsg, outDate);
        // TODO: Add assertions for OUT parameters (values depend on test data)
        // assertNotNull(outFlag.get());
        // assertNotNull(outMsg.get());
        // assertNotNull(outDate.get());
    }

    @org.springframework.test.context.jdbc.Sql(scripts = "classpath:itest-fixtures/PKG_2008802001_MGT_proc_get_respond_date.sql", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))
    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    void test_procGetRespondDate_integration() {
        String inAccntId = "test_inAccntId";
        String inAccntDate = "2024-01-01";
        String inSeqNo = "test_inSeqNo";
        Long inInterfaceSeq = 100L;
        AtomicReference<Long> outFlag = new AtomicReference<>(null);
        AtomicReference<String> outMsg = new AtomicReference<>(null);
        AtomicReference<String> outDate = new AtomicReference<>(null);
        _2008802001MgtService.procGetRespondDate(inAccntId, inAccntDate, inSeqNo, inInterfaceSeq, outFlag, outMsg, outDate);
        // TODO: Add assertions for OUT parameters (values depend on test data)
        // assertNotNull(outFlag.get());
        // assertNotNull(outMsg.get());
        // assertNotNull(outDate.get());
    }
}
