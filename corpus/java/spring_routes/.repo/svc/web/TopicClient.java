package svc.web;

import org.springframework.web.bind.annotation.GetMapping;

public interface TopicClient {

    @GetMapping("/remote/topics")
    String remote();
}
