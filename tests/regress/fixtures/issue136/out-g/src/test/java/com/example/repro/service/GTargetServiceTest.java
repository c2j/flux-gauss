package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.GTargetMapper;
import com.example.repro.service.GTargetService;
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
// Source: g-arity.sql
class GTargetServiceTest {

    @Mock
    private GTargetMapper gTargetMapper;

    @InjectMocks
    private GTargetService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_fncWithDefaults_success() {
        String pA = "test_p_a";
        String pB = "test_p_b";
        String pC = "test_p_c";
        var result = service.fncWithDefaults(pA, pB, pC);
    }
}
