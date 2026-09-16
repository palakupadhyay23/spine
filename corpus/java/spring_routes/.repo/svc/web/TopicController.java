package svc.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class TopicController {

    @GetMapping("/topics")
    public String list() { return "topics"; }

    @PostMapping("/topics")
    public String create() { return "created"; }

    @GetMapping
    public String root() { return "api"; }

    @RequestMapping(value = "/topics/{id}", method = {RequestMethod.PUT, RequestMethod.PATCH})
    public String replace() { return "replaced"; }

    @RequestMapping(path = "/topics/{id}/archive", method = RequestMethod.POST)
    public String archive() { return "archived"; }

    @RequestMapping("/topics/anything")
    public String anything() { return "any"; }

    public String helper() { return "not a route"; }
}
