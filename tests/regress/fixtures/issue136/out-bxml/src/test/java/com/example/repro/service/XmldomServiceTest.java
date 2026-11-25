package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.XmldomMapper;
import com.example.repro.service.XmldomService;
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
// Source: b-xml.sql
class XmldomServiceTest {

    @Mock
    private XmldomMapper xmldomMapper;

    @InjectMocks
    private XmldomService service;

    @Test
    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)
    void test_getnodevalue_success() {
        Object pNode = new java.util.HashMap<String, Object>();
        var result = service.getnodevalue(pNode);
    }
}
