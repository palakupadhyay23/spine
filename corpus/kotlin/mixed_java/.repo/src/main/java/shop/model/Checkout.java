package shop.model;

public class Checkout {
    public int total(Cart cart) {
        return cart.price();
    }
}
