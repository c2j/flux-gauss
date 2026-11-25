package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.ReproMapper;
import com.example.repro.service.ReproService;
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
// Source: a-null-numeric.sql
class ReproServiceTest {

    @Mock
    private ReproMapper reproMapper;

    @InjectMocks
    private ReproService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_fncCalc_success() {
        Long pA = 100L;
        Long pB = 100L;
        Long pC = 100L;
        var result = service.fncCalc(pA, pB, pC);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_prcCheckNull_success() {
        Long pValue = 100L;
        service.prcCheckNull(pValue);
    }
}
