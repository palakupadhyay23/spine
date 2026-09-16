package svc.web;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.*;

@Controller
public class PageController {

    @GetMapping("/")
    public String welcome() { return "welcome"; }

    @GetMapping(value = "vets.json", produces = "application/json")
    public String vets() { return "vets"; }
}
