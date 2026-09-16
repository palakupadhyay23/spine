package shop.nav

import androidx.navigation.NavController
import androidx.navigation.NavGraphBuilder
import androidx.navigation.compose.composable

const val cartRoute = "cart_route"
const val itemIdArg = "itemId"

fun NavController.navigateToCart() {
    this.navigate(cartRoute)
}

fun NavController.navigateToItem(id: String) {
    this.navigate("item_route/$id")
}

fun NavGraphBuilder.cartScreen() {
    composable(route = cartRoute) {
        CartRoute()
    }
}

fun NavGraphBuilder.itemScreen() {
    composable(route = "item_route/{$itemIdArg}") {
        ItemRoute()
    }
}

fun NavGraphBuilder.splitScreen() {
    composable(route = "split_route") {
        LeftPane()
        RightPane()
    }
}

fun NavGraphBuilder.computedScreen() {
    composable(route = buildRoute()) {
        HiddenRoute()
    }
}

fun CartRoute() {}

fun ItemRoute() {}

fun LeftPane() {}

fun RightPane() {}

fun HiddenRoute() {}

fun buildRoute(): String = "nope"
