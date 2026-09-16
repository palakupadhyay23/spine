package shop.api;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;

@Path("/v1")
public class TopicResource {

    @GET
    @Path("/topics")
    public String listTopics() {
        return "[]";
    }
}
