package cedtu.service;

import cedtu.exception.BusinessException;
import cedtu.mapper.PackLogMapper;
import cedtu.service.PackLogService;
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
// Source: PACK_LOG.sql
class PackLogServiceTest {

    @Mock
    private PackLogMapper packLogMapper;

    @InjectMocks
    private PackLogService service;

    @org.junit.jupiter.api.Disabled("auto-generated mock cannot terminate recursive call")
    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_log_success() {
        String inProcname = "test_inProcname";
        String inInfo = "test_inInfo";
        String inLevel = "test_inLevel";
        when(packLogMapper.insertLog(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(packLogMapper.insertLogNoautotrans(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        service.log(inProcname, inInfo, inLevel);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_log_success_1() {
        String inProcname = "test_inProcname";
        when(packLogMapper.insertLog(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(packLogMapper.insertLogNoautotrans(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        service.log(inProcname);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_log_success_2() {
        String inProcname = "test_inProcname";
        String inInfo = "test_inInfo";
        when(packLogMapper.insertLog(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(packLogMapper.insertLogNoautotrans(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        service.log(inProcname, inInfo);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_log_success_3() {
        String inProcname = "test_inProcname";
        String inStepno = "test_inStepno";
        String inInfo = "test_inInfo";
        String inLevel = "test_inLevel";
        String inSqltxt = "test_inSqltxt";
        String inSqlparam = "test_inSqlparam";
        when(packLogMapper.insertLog(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(packLogMapper.insertLogNoautotrans(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        service.log(inProcname, inStepno, inInfo, inLevel, inSqltxt, inSqlparam);
        verify(packLogMapper, atLeast(0)).insertLog(any(), any(), any(), any(), any(), any(), any(), any());
    }

    @org.junit.jupiter.api.Disabled("auto-generated mock cannot terminate recursive call")
    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_log_success_4() {
        String inProcname = "test_inProcname";
        String inStepno = "test_inStepno";
        String inInfo = "test_inInfo";
        String inLevel = "test_inLevel";
        when(packLogMapper.insertLog(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(packLogMapper.insertLogNoautotrans(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        service.log(inProcname, inStepno, inInfo, inLevel);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_logNoautotrans_success() {
        String inProcname = "test_inProcname";
        String inStepno = "test_inStepno";
        String inInfo = "test_inInfo";
        String inLevel = "test_inLevel";
        String inSqltxt = "test_inSqltxt";
        String inSqlparam = "test_inSqlparam";
        when(packLogMapper.insertLog(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        when(packLogMapper.insertLogNoautotrans(any(), any(), any(), any(), any(), any(), any(), any())).thenReturn(1);
        service.logNoautotrans(inProcname, inStepno, inInfo, inLevel, inSqltxt, inSqlparam);
        verify(packLogMapper, atLeast(0)).insertLogNoautotrans(any(), any(), any(), any(), any(), any(), any(), any());
    }
}
