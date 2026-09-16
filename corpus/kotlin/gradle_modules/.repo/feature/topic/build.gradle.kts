val shared = ":core:data"

plugins {
    id("shop.android.feature")
}

dependencies {
    api(project(":core:data"))
    implementation(project(shared))
    implementation(libs.androidx.core.ktx)
}
