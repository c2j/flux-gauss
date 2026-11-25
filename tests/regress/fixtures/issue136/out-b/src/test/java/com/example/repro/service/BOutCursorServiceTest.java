package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.BOutCursorMapper;
import com.example.repro.service.BOutCursorService;
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
// Source: b-out-cursor.sql
class BOutCursorServiceTest {

    @Mock
    private BOutCursorMapper bOutCursorMapper;

    @InjectMocks
    private BOutCursorService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_prcOutCursor_success() {
        { var m = new java.util.HashMap<String,Object>(); m.put("id", 1L); when(bOutCursorMapper.selectPrcOutCursor()).thenReturn(java.util.List.of(m)); }
        var result = service.prcOutCursor();
        assertNotNull(result);
    }
}
