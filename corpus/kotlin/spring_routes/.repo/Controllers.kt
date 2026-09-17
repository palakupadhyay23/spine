package shop.api

import org.springframework.stereotype.Controller
import org.springframework.web.bind.annotation.*

const val ADMIN_PREFIX = "/admin"

@RestController
@RequestMapping(ADMIN_PREFIX)
class AdminController(val repo: TopicRepository) {

    @GetMapping("/purge")
    fun purge(): String = "purged"
}

@RestController
@RequestMapping("/api")
class TopicController(val repo: TopicRepository) {

    @GetMapping("/topics")
    fun list(): String = "topics"

    @PostMapping("/topics")
    fun create(): String = "created"

    @GetMapping
    fun root(): String = "api"

    @RequestMapping(value = "/topics/{id}", method = [RequestMethod.PUT])
    fun replace(): String = "replaced"

    @RequestMapping("/topics/anything")
    fun anything(): String = "any"

    fun helper(): String = "not a route"
}

@Controller
class PageController {

    @GetMapping("/")
    fun welcome(): String = "welcome"

    @GetMapping("vets.json", produces = ["application/json"])
    fun vets(): String = "vets"
}

interface TopicClient {

    @GetMapping("/remote/topics")
    fun remote(): String
}

interface TopicRepository
