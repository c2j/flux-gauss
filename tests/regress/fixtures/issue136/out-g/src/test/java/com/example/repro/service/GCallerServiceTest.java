package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.GCallerMapper;
import com.example.repro.service.GCallerService;
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
class GCallerServiceTest {

    @Mock
    private GCallerMapper gCallerMapper;

    @Mock
    private GTargetService gTargetService;

    @InjectMocks
    private GCallerService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_prcCaller_success() {
        when(gTargetService.fncWithDefaults((String) any(), (String) any(), (String) any())).thenReturn("1");
        service.prcCaller();
    }
}
