package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.XmldomMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: b-xml.sql
public class XmldomService {
    private static final Logger log = LoggerFactory.getLogger(XmldomService.class);

    private final XmldomMapper xmldomMapper;

    public XmldomService(XmldomMapper xmldomMapper) {
        this.xmldomMapper = xmldomMapper;
    }

    // Source: PKG_XMLDOM.getnodevalue (FUNCTION) — b-xml.sql:7-13
    public String getnodevalue(Object pNode) {
        String vValue = null;
        vValue = /* TOBEFIX: unresolved fn GETNODEVALUE(pNode) - pkg=DBE_XMLDOM, caller=b-xml.sql:getnodevalue */ null;
        vValue = /* TOBEFIX: unresolved fn GETCHILDNODES(pNode) - pkg=DBE_XMLDOM, caller=b-xml.sql:getnodevalue */ null;
        return String.valueOf(vValue);
    }
}
