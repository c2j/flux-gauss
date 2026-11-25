package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.CCallerMapper;
import com.example.repro.service.CCalleeService;
import com.example.repro.service.CCallerService;
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
// Source: c-atomic.sql
class CCallerServiceTest {

    @Mock
    private CCallerMapper cCallerMapper;

    @Mock
    private CCalleeService cCalleeService;

    @InjectMocks
    private CCallerService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_prcCaller_success() {
        AtomicReference<String> pCodeRef = new AtomicReference<>(null);
        service.prcCaller(pCodeRef);
    }
}
