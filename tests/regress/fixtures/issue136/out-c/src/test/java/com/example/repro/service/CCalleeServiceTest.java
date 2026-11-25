package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.CCalleeMapper;
import com.example.repro.service.CCalleeService;
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
// Source: c-atomic.sql
class CCalleeServiceTest {

    @Mock
    private CCalleeMapper cCalleeMapper;

    @InjectMocks
    private CCalleeService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_prcLog_success_1() {
        String pA = "test_p_a";
        String pB = "test_p_b";
        String pC = "test_p_c";
        service.prcLog(pA, pB, pC);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_prcLog_success_2() {
        String pA = "test_p_a";
        String pB = "test_p_b";
        String pC = "test_p_c";
        String pD = "test_p_d";
        String pE = "test_p_e";
        String pF = "test_p_f";
        service.prcLog(pA, pB, pC, pD, pE, pF);
    }
}
