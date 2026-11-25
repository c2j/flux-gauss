package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.DCoercionMapper;
import com.example.repro.service.DCoercionService;
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
// Source: d-coercion.sql
class DCoercionServiceTest {

    @Mock
    private DCoercionMapper dCoercionMapper;

    @InjectMocks
    private DCoercionService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_fncText_success() {
        String pValue = "test_p_value";
        var result = service.fncText(pValue);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_fncDecimal_success() {
        String pValue = "test_p_value";
        var result = service.fncDecimal(pValue);
    }

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_prcAssign_success() {
        Long pNumber = 100L;
        service.prcAssign(pNumber);
    }
}
