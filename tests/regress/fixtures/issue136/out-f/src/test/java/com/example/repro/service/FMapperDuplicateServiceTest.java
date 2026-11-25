package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.FMapperDuplicateMapper;
import com.example.repro.service.FMapperDuplicateService;
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
// Source: f-mapper-duplicate.sql
class FMapperDuplicateServiceTest {

    @Mock
    private FMapperDuplicateMapper fMapperDuplicateMapper;

    @InjectMocks
    private FMapperDuplicateService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_prcDynamic_success() {
        Long pId = 1L;
        { var m = new java.util.HashMap<String,Object>(); m.put("1", 1L); when(fMapperDuplicateMapper.selectPrcDynamic((Long) any(), (Long) any())).thenReturn(m); }
        service.prcDynamic(pId);
    }
}
